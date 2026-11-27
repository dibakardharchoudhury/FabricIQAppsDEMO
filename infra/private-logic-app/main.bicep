targetScope = 'resourceGroup'

param location string = 'swedencentral'
param vnetName string = 'vnet_dbfabric'
param appName string = 'logic-dbfabric-private-${uniqueString(resourceGroup().id)}'
param storageName string = 'stlogic${uniqueString(resourceGroup().id)}'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}
resource helperNat 'Microsoft.Network/natGateways@2024-05-01' existing = {
  name: 'nat-aigproxy'
}

resource integration 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: 'snet-logic-integration'
  properties: {
    addressPrefix: '10.0.5.0/26'
    delegations: [
      {
        name: 'web'
        properties: { serviceName: 'Microsoft.Web/serverFarms' }
      }
    ]
  }
}

resource endpoints 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: 'snet-logic-endpoints'
  properties: {
    addressPrefix: '10.0.6.0/27'
    privateEndpointNetworkPolicies: 'Disabled'
  }
  dependsOn: [integration]
}

resource helperSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: 'snet-logic-deploy'
  properties: {
    addressPrefix: '10.0.7.0/28'
    natGateway: { id: helperNat.id }
    delegations: [
      {
        name: 'aci'
        properties: { serviceName: 'Microsoft.ContainerInstance/containerGroups' }
      }
    ]
  }
  dependsOn: [integration, endpoints]
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    networkAcls: { defaultAction: 'Deny', bypass: 'None' }
  }
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  name: '${storage.name}/default/content'
  properties: { shareQuota: 5 }
}

var services = ['blob', 'file', 'queue', 'table']
resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for service in services: {
  name: 'privatelink.${service}.core.windows.net'
  location: 'global'
}]
resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (service, i) in services: {
  parent: zones[i]
  name: 'vnet-dbfabric-logic'
  location: 'global'
  properties: {
    registrationEnabled: false
    resolutionPolicy: 'NxDomainRedirect'
    virtualNetwork: { id: vnet.id }
  }
}]
resource storageEndpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [for service in services: {
  name: 'pe-${storage.name}-${service}'
  location: location
  properties: {
    subnet: { id: endpoints.id }
    privateLinkServiceConnections: [
      {
        name: service
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [service]
        }
      }
    ]
  }
}]
resource storageDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (service, i) in services: {
  parent: storageEndpoints[i]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: service
        properties: { privateDnsZoneId: zones[i].id }
      }
    ]
  }
}]

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: 'asp-${appName}'
  location: location
  sku: { name: 'WS1', tier: 'WorkflowStandard', capacity: 1 }
  kind: 'elastic'
  properties: { reserved: false }
}

var storageConnection = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
resource app 'Microsoft.Web/sites@2024-11-01' = {
  name: appName
  location: location
  kind: 'functionapp,workflowapp'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    publicNetworkAccess: 'Disabled'
    httpsOnly: true
    virtualNetworkSubnetId: integration.id
    outboundVnetRouting: {
      allTraffic: true
      contentShareTraffic: true
    }
    siteConfig: {
      alwaysOn: true
      use32BitWorkerProcess: false
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      vnetRouteAllEnabled: true
      appSettings: [
        { name: 'APP_KIND', value: 'workflowApp' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'dotnet' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~22' }
        { name: 'AzureWebJobsStorage', value: storageConnection }
        { name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING', value: storageConnection }
        { name: 'WEBSITE_CONTENTSHARE', value: 'content' }
        { name: 'WEBSITE_CONTENTOVERVNET', value: '1' }
        { name: 'WEBSITE_SKIP_CONTENTSHARE_VALIDATION', value: '1' }
      ]
    }
  }
  dependsOn: [share, links, storageDns]
}
resource ftpAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: app
  name: 'ftp'
  properties: { allow: false }
}
resource scmAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: app
  name: 'scm'
  properties: { allow: false }
}

resource appZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.azurewebsites.net'
  location: 'global'
}
resource appLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: appZone
  name: 'vnet-dbfabric-logic'
  location: 'global'
  properties: {
    registrationEnabled: false
    resolutionPolicy: 'NxDomainRedirect'
    virtualNetwork: { id: vnet.id }
  }
}
resource appEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${app.name}'
  location: location
  properties: {
    subnet: { id: endpoints.id }
    privateLinkServiceConnections: [
      {
        name: 'sites'
        properties: {
          privateLinkServiceId: app.id
          groupIds: ['sites']
        }
      }
    ]
  }
}
resource appDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: appEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'sites'
        properties: { privateDnsZoneId: appZone.id }
      }
    ]
  }
}

output appId string = app.id
output appHostname string = '${app.name}.azurewebsites.net'
output storageId string = storage.id
output helperSubnetId string = helperSubnet.id
