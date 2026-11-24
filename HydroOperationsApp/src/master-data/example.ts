import type { Contract, Samples } from './contract'

// Explicit opt-in training fixture, never used as a fallback for a live source.
export function exampleContract(): Contract {
  const workspaceId = '00000000-0000-0000-0000-000000000001'
  const lakehouseId = '00000000-0000-0000-0000-000000000002'
  return {
    formatVersion: 1, name: 'Customer 360',
    ontology: { workspaceId, itemId: '00000000-0000-0000-0000-000000000003', generation: 2, definitionHash: '' },
    entities: [
      { id: 'customer', ontologyEntity: 'Customer', label: 'Customer', goldTable: 'customer', key: 'customer_id', properties: [
        { name: 'customer_id', ontologyProperty: 'customer_id', label: 'Customer ID', type: 'string', required: true, strategy: 'require_equal', priority: [] },
        { name: 'customer_name', ontologyProperty: 'name', label: 'Customer name', type: 'string', required: true, strategy: 'source_priority', priority: ['crm', 'erp'] },
        { name: 'email', ontologyProperty: 'email', label: 'Email', type: 'string', required: false, strategy: 'require_equal', priority: [] },
      ] },
      { id: 'sales_order', ontologyEntity: 'SalesOrder', label: 'Sales order', goldTable: 'sales_order', key: 'order_id', properties: [
        { name: 'order_id', ontologyProperty: 'order_id', label: 'Order ID', type: 'string', required: true, strategy: 'require_equal', priority: [] },
        { name: 'customer_id', ontologyProperty: 'customer_id', label: 'Customer ID', type: 'string', required: true, strategy: 'require_equal', priority: [] },
      ] },
    ],
    sources: [
      { id: 'crm', label: 'Dynamics CRM', entity: 'customer', workspaceId, lakehouseId, schema: 'silver', table: 'crm_customer',
        keyColumn: 'customer_id', keyNamespace: 'enterprise_customer_id', columns: [
          { name: 'customer_id', type: 'string' }, { name: 'customer', type: 'string' },
          { name: 'email', type: 'string' }, { name: 'loyalty_tier', type: 'string' },
        ] },
      { id: 'erp', label: 'SAP ERP', entity: 'customer', workspaceId, lakehouseId, schema: 'silver', table: 'erp_customer',
        keyColumn: 'customer_id', keyNamespace: 'enterprise_customer_id', columns: [
          { name: 'customer_id', type: 'string' }, { name: 'customer', type: 'string' }, { name: 'email', type: 'string' },
        ] },
      { id: 'orders', label: 'Order management', entity: 'sales_order', workspaceId, lakehouseId, schema: 'silver', table: 'sales_order',
        keyColumn: 'order_id', keyNamespace: 'enterprise_order_id', columns: [
          { name: 'order_id', type: 'string' }, { name: 'customer_id', type: 'string' },
        ] },
    ],
    mappings: [
      ...['crm', 'erp'].flatMap(source => [
        { source, column: 'customer_id', target: 'customer_id', transform: 'identity' as const, reason: 'Shared enterprise ID, conformed in Silver.', reviewed: true },
        { source, column: 'customer', target: 'customer_name', transform: 'trim' as const, reason: 'Customer is the display name, not the matching key.', reviewed: false },
        { source, column: 'email', target: 'email', transform: 'lower' as const, reason: 'Normalize casing; conflicting addresses require a steward decision.', reviewed: true },
      ]),
      ...['order_id', 'customer_id'].map(column => ({
        source: 'orders', column, target: column, transform: 'identity' as const,
        reason: 'Conformed enterprise key.', reviewed: true,
      })),
    ],
    relationships: [{ name: 'order_customer', ontologyRelationship: 'OrderCustomer', from: 'sales_order', column: 'customer_id', to: 'customer' }],
  }
}
export const exampleSamples: Samples = {
  crm: [
    { customer_id: 'C-1001', customer: 'Northwind Energy', email: 'HELLO@NORTHWIND.EXAMPLE', loyalty_tier: 'Gold' },
    { customer_id: 'C-1002', customer: 'Contoso Renewables', email: 'contact@contoso.example', loyalty_tier: 'Silver' },
  ],
  erp: [
    { customer_id: 'C-1001', customer: 'Northwind Energy AS', email: 'hello@northwind.example' },
    { customer_id: 'C-1002', customer: 'Contoso Renewables Ltd', email: 'billing@contoso.example' },
    { customer_id: 'C-1003', customer: 'Fabrikam Grid', email: null },
  ],
  orders: [{ order_id: 'SO-4001', customer_id: 'C-1001' }, { order_id: 'SO-4002', customer_id: 'C-1002' }],
}
