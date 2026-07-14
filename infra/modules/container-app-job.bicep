// Container Apps Job — Schema Extraction (triggered manually from Admin UI)

param name string
param location string
param containerAppsEnvironmentId string
param acrLoginServer string
param storageAccountName string
param keyVaultName string

resource schemaJob 'Microsoft.App/jobs@2023-05-01' = {
  name: name
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 1
      registries: [
        {
          server: acrLoginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'schema-extractor'
          image: '${acrLoginServer}/nltodax-schema-extractor:latest'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'AZURE_STORAGE_ACCOUNT_NAME'
              value: storageAccountName
            }
            {
              name: 'AZURE_KEY_VAULT_NAME'
              value: keyVaultName
            }
            {
              name: 'SCHEMAS_CONTAINER'
              value: 'schemas'
            }
          ]
        }
      ]
    }
  }
}

// RBAC: Grant job write access to storage (to save extracted schemas)
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

resource storageBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(schemaJob.id, storageAccount.id, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: schemaJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: Grant Key Vault access (for Power BI credentials)
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(schemaJob.id, keyVault.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: schemaJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output jobName string = schemaJob.name
output principalId string = schemaJob.identity.principalId
