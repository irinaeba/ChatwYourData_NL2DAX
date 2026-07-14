// ============================================================
// NLtoDAX Production Infrastructure
// Deploys: Container Apps Env, Storage, ACR, Key Vault, Log Analytics
// ============================================================

targetScope = 'resourceGroup'

// ─── Parameters ─────────────────────────────────────────────
@description('Environment name (dev, staging, prod)')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'prod'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Base name for resources')
param appName string = 'nltodax'

@description('Container image tag for the main app')
param mainAppImageTag string = 'latest'

@description('Container image tag for the admin portal')
param adminAppImageTag string = 'latest'

// ─── Variables ──────────────────────────────────────────────
var resourcePrefix = '${appName}-${environment}'
var storageAccountName = replace('stor${appName}${environment}', '-', '')
var acrName = replace('acr${appName}${environment}', '-', '')
var keyVaultName = 'kv-${appName}-${environment}'
var logAnalyticsName = 'log-${resourcePrefix}'
var containerEnvName = 'cae-${resourcePrefix}'

// ─── Modules ────────────────────────────────────────────────

module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'logAnalytics'
  params: {
    name: logAnalyticsName
    location: location
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    storageAccountName: storageAccountName
    location: location
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    acrName: acrName
    location: location
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyVault'
  params: {
    keyVaultName: keyVaultName
    location: location
  }
}

module containerAppsEnv 'modules/container-apps-env.bicep' = {
  name: 'containerAppsEnv'
  params: {
    name: containerEnvName
    location: location
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
  }
}

module mainApp 'modules/container-app-main.bicep' = {
  name: 'mainApp'
  params: {
    name: 'ca-${resourcePrefix}'
    location: location
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    acrLoginServer: acr.outputs.loginServer
    imageTag: mainAppImageTag
    storageAccountName: storage.outputs.storageAccountName
    keyVaultName: keyVault.outputs.keyVaultName
  }
}

module adminApp 'modules/container-app-admin.bicep' = {
  name: 'adminApp'
  params: {
    name: 'ca-${resourcePrefix}-admin'
    location: location
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    acrLoginServer: acr.outputs.loginServer
    imageTag: adminAppImageTag
    storageAccountName: storage.outputs.storageAccountName
    keyVaultName: keyVault.outputs.keyVaultName
    mainAppFqdn: mainApp.outputs.fqdn
  }
}

module schemaExtractionJob 'modules/container-app-job.bicep' = {
  name: 'schemaExtractionJob'
  params: {
    name: 'job-${resourcePrefix}-schema'
    location: location
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    acrLoginServer: acr.outputs.loginServer
    storageAccountName: storage.outputs.storageAccountName
    keyVaultName: keyVault.outputs.keyVaultName
  }
}

// ─── Outputs ────────────────────────────────────────────────
output mainAppUrl string = 'https://${mainApp.outputs.fqdn}'
output adminAppUrl string = 'https://${adminApp.outputs.fqdn}'
output storageAccountName string = storage.outputs.storageAccountName
output acrLoginServer string = acr.outputs.loginServer
output keyVaultName string = keyVault.outputs.keyVaultName
