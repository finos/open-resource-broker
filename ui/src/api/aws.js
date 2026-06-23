import client from './client'

// Returns { region, vpcs, subnets, security_groups, amis }
export const getAwsDiscovery = async (region = 'us-east-1') => {
  const { data } = await client.get('/aws/discovery', { params: { region } })
  return data
}

// Confirms termination on EC2 and tags shutdown machines.
// Returns { region, results: [{ machineId, ok, state, tagged, shutdownAt? , message? , error? }] }
export const confirmShutdown = async (machineIds, region = 'us-east-1') => {
  const { data } = await client.post(
    '/aws/confirm-shutdown',
    { machineIds },
    { params: { region } }
  )
  return data
}

// Creates or updates an AWS EC2 Launch Template to mirror an ORB template.
// Called after create and update so the AWS account stays in sync.
export const syncAwsTemplate = async (templatePayload, region = 'us-east-1') => {
  const { data } = await client.post(
    '/aws/sync-template',
    templatePayload,
    { params: { region } }
  )
  return data
}

// Deletes any AWS EC2 Launch Templates tagged orb:template-id=<id> so deleting
// an ORB template doesn't leave orphans on the AWS side.
export const cleanupAwsTemplate = async (templateId, region = 'us-east-1') => {
  const { data } = await client.post(
    '/aws/cleanup-template',
    { templateId },
    { params: { region } }
  )
  return data
}

// Static curated AWS region list
export const getAwsRegions = async () => {
  const { data } = await client.get('/aws/regions')
  return data
}

// UI defaults (region/vpc/subnet/sg) persisted server-side
export const getUiDefaults = async () => {
  const { data } = await client.get('/config/defaults')
  return data
}

export const saveUiDefaults = async (defaults) => {
  const { data } = await client.put('/config/defaults', defaults)
  return data
}
