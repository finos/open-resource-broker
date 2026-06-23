import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as templatesApi from '../api/templates'
import * as machinesApi from '../api/machines'
import * as requestsApi from '../api/requests'
import * as systemApi from '../api/system'
import * as awsApi from '../api/aws'

// ── Templates ──────────────────────────────────────────────────────────────

export const useTemplates = () =>
  useQuery({
    queryKey: ['templates'],
    queryFn: templatesApi.getTemplates,
    staleTime: 60_000,
  })

export const useTemplate = (id) =>
  useQuery({
    queryKey: ['templates', id],
    queryFn: () => templatesApi.getTemplate(id),
    enabled: !!id,
  })

export const useValidateTemplate = () =>
  useMutation({ mutationFn: templatesApi.validateTemplate })

export const useRefreshTemplates = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: templatesApi.refreshTemplates,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }),
  })
}

// ── Machines ───────────────────────────────────────────────────────────────

export const useMachines = () =>
  useQuery({
    queryKey: ['machines'],
    queryFn: machinesApi.getMachines,
    refetchInterval: 15_000,
  })

export const useMachine = (id) =>
  useQuery({
    queryKey: ['machines', id],
    queryFn: () => machinesApi.getMachine(id),
    enabled: !!id,
    refetchInterval: 15_000,
  })

// ── Requests ───────────────────────────────────────────────────────────────

export const useRequests = (filter) =>
  useQuery({
    queryKey: ['requests'],
    queryFn: requestsApi.getRequests,
    refetchInterval: 10_000,
    select: (data) => {
      const list = Array.isArray(data) ? data : data?.requests || []
      if (!filter || filter === 'all') return list
      return list.filter((r) => r.status === filter)
    },
  })

// Returns-only filtered list (GET /requests/return)
export const useReturnRequests = (limit = 100) =>
  useQuery({
    queryKey: ['requests', 'return', limit],
    queryFn: () => requestsApi.getReturnRequests(limit),
    refetchInterval: 15_000,
    select: (data) => (Array.isArray(data) ? data : data?.requests || []),
  })

export const useRequest = (id) =>
  useQuery({
    queryKey: ['requests', id],
    queryFn: () => requestsApi.getRequest(id),
    enabled: !!id,
    refetchInterval: 5_000,
  })

// ── System ─────────────────────────────────────────────────────────────────

export const useSystemHealth = () =>
  useQuery({
    queryKey: ['health'],
    queryFn: systemApi.getHealth,
    refetchInterval: 30_000,
    retry: 1,
  })

export const useSystemInfo = () =>
  useQuery({
    queryKey: ['info'],
    queryFn: systemApi.getInfo,
    staleTime: 60_000,
    retry: 1,
  })

export const useMetrics = () =>
  useQuery({
    queryKey: ['metrics'],
    queryFn: systemApi.getMetrics,
    refetchInterval: 30_000,
    retry: 1,
  })

// ── AWS ────────────────────────────────────────────────────────────────────

export const useAwsDiscovery = (region = 'us-east-1') =>
  useQuery({
    queryKey: ['aws-discovery', region],
    queryFn: () => awsApi.getAwsDiscovery(region),
    staleTime: 5 * 60_000,
    retry: 1,
  })

export const useAwsRegions = () =>
  useQuery({
    queryKey: ['aws-regions'],
    queryFn: awsApi.getAwsRegions,
    staleTime: Infinity,
    retry: 1,
  })

export const useUiDefaults = () =>
  useQuery({
    queryKey: ['ui-defaults'],
    queryFn: awsApi.getUiDefaults,
    staleTime: 30_000,
  })

export const useSaveUiDefaults = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: awsApi.saveUiDefaults,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ui-defaults'] }),
  })
}

// ── Mutations ──────────────────────────────────────────────────────────────

// createRequest: { templateId, count }
//
// Before submitting, if the template lacks subnet/SG values, apply the saved
// UI defaults (Config page) to the template via PUT. This makes the 20 built-in
// templates usable out of the box once the user has saved their defaults.
export const useCreateRequest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ templateId, count }) => {
      try {
        const [tplRes, defRes] = await Promise.all([
          templatesApi.getTemplate(templateId),
          awsApi.getUiDefaults().catch(() => ({ defaults: {} })),
        ])
        const tpl = tplRes?.template || tplRes
        const defaults = defRes?.defaults || {}
        const needsSubnet = !tpl?.subnet_ids || tpl.subnet_ids.length === 0
        const needsSg =
          !tpl?.security_group_ids || tpl.security_group_ids.length === 0

        const patch = {}
        if (needsSubnet && defaults.subnetId) patch.subnetIds = [defaults.subnetId]
        if (needsSg && defaults.securityGroupId)
          patch.securityGroupIds = [defaults.securityGroupId]

        if (Object.keys(patch).length > 0) {
          await templatesApi.updateTemplate(templateId, patch)
        }
      } catch {
        // Best-effort: if defaults lookup or PUT fails, continue and let the
        // request endpoint surface a clear validation error.
      }
      return requestsApi.createRequest({ templateId, count })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['requests'] })
      qc.invalidateQueries({ queryKey: ['machines'] })
      qc.invalidateQueries({ queryKey: ['templates'] })
    },
  })
}

export const useReturnMachines = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids) => machinesApi.returnMachines(ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['machines'] })
      qc.invalidateQueries({ queryKey: ['requests'] })
    },
  })
}

export const useCancelRequest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => requestsApi.cancelRequest(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['requests'] }),
  })
}
