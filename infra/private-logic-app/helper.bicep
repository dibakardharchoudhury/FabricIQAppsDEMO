param appName string
param storageName string
param helperSubnetId string
param scriptBase64 string
param packageBase64 string
param location string = 'swedencentral'

resource app 'Microsoft.Web/sites@2024-11-01' existing = { name: appName }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageName }
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-logic-private-deploy'
  location: location
}
resource appRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: app
  name: guid(app.id, identity.id, 'website-contributor')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'de139f84-1756-47ae-9be6-808fbbe84772')
  }
}
resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, identity.id, 'storage-key-operator')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '81a9662b-bebf-436f-a333-f67b29880f12')
  }
}
resource helper 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: 'aci-logic-private-deploy'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    osType: 'Linux'
    restartPolicy: 'Never'
    subnetIds: [{ id: helperSubnetId }]
    containers: [
      {
        name: 'deploy'
        properties: {
          image: 'mcr.microsoft.com/azure-cli:2.84.0'
          command: [
            '/bin/sh'
            '-c'
            'echo "$SCRIPT_B64" | base64 -d > /tmp/helper.py && python3 /tmp/helper.py'
          ]
          resources: { requests: { cpu: 1, memoryInGB: 2 } }
          environmentVariables: [
            { name: 'IDENTITY_CLIENT_ID', value: identity.properties.clientId }
            { name: 'APP_ID', value: app.id }
            { name: 'STORAGE_ID', value: storage.id }
            { name: 'SCRIPT_B64', value: scriptBase64 }
            { name: 'PACKAGE_B64', value: packageBase64 }
          ]
        }
      }
    ]
  }
  dependsOn: [appRole, storageRole]
}
output helperId string = helper.id
output identityId string = identity.id
output appRoleId string = appRole.id
output storageRoleId string = storageRole.id
