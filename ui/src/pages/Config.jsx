import { useEffect, useMemo, useState } from 'react'
import {
  useAwsRegions,
  useAwsDiscovery,
  useUiDefaults,
  useSaveUiDefaults,
} from '../hooks'
import { useToast } from '../components/Toast'
import Topbar from '../components/Topbar'

const inputCls =
  'w-full border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-[#185FA5]'

function Field({ label, hint, children }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-0.5">{hint}</p>}
    </div>
  )
}

export default function Config() {
  const { show } = useToast()
  const { data: regionsData, isLoading: regionsLoading } = useAwsRegions()
  const { data: defaultsData, isLoading: defaultsLoading } = useUiDefaults()
  const saveDefaults = useSaveUiDefaults()

  const regions = regionsData?.regions || []
  const saved = defaultsData?.defaults || {}

  // Local form state
  const [region, setRegion] = useState('')
  const [vpcId, setVpcId] = useState('')
  const [subnetId, setSubnetId] = useState('')
  const [sgId, setSgId] = useState('')
  const [hydrated, setHydrated] = useState(false)

  // Hydrate once defaults arrive
  useEffect(() => {
    if (defaultsLoading || hydrated) return
    setRegion(saved.region || 'us-east-1')
    setVpcId(saved.vpcId || '')
    setSubnetId(saved.subnetId || '')
    setSgId(saved.securityGroupId || '')
    setHydrated(true)
  }, [defaultsLoading, hydrated, saved])

  // Pull live infrastructure for the selected region
  const {
    data: discovery,
    isLoading: discoveryLoading,
    error: discoveryError,
    refetch: refetchDiscovery,
  } = useAwsDiscovery(region || 'us-east-1')

  const vpcs = discovery?.vpcs || []
  const subnets = discovery?.subnets || []
  const securityGroups = discovery?.security_groups || []

  // When region changes (and we have data), pick a sensible default VPC if needed
  useEffect(() => {
    if (!hydrated || vpcs.length === 0) return
    if (!vpcs.find((v) => v.id === vpcId)) {
      const def = vpcs.find((v) => v.is_default) || vpcs[0]
      setVpcId(def.id)
    }
  }, [vpcs, vpcId, hydrated])

  const subnetsForVpc = useMemo(
    () => subnets.filter((s) => !vpcId || s.vpc_id === vpcId),
    [subnets, vpcId]
  )
  const sgsForVpc = useMemo(
    () => securityGroups.filter((g) => !vpcId || g.vpc_id === vpcId),
    [securityGroups, vpcId]
  )

  // Reset subnet/SG when VPC changes if the saved one no longer applies
  useEffect(() => {
    if (!hydrated) return
    if (subnetsForVpc.length && !subnetsForVpc.find((s) => s.id === subnetId)) {
      setSubnetId(subnetsForVpc[0].id)
    } else if (subnetsForVpc.length === 0) {
      setSubnetId('')
    }
  }, [subnetsForVpc, subnetId, hydrated])

  useEffect(() => {
    if (!hydrated) return
    if (sgsForVpc.length && !sgsForVpc.find((g) => g.id === sgId)) {
      const def = sgsForVpc.find((g) => g.name === 'default') || sgsForVpc[0]
      setSgId(def.id)
    } else if (sgsForVpc.length === 0) {
      setSgId('')
    }
  }, [sgsForVpc, sgId, hydrated])

  const handleSave = async () => {
    const payload = { region, vpcId, subnetId, securityGroupId: sgId }
    try {
      await saveDefaults.mutateAsync(payload)
      show({ type: 'success', message: 'Defaults saved' })
    } catch (err) {
      show({ type: 'error', message: err.message || 'Save failed' })
    }
  }

  const handleClear = async () => {
    try {
      await saveDefaults.mutateAsync({})
      setRegion('us-east-1')
      setVpcId('')
      setSubnetId('')
      setSgId('')
      show({ type: 'success', message: 'Defaults cleared' })
    } catch (err) {
      show({ type: 'error', message: err.message || 'Failed to clear' })
    }
  }

  const isDirty =
    region !== (saved.region || '') ||
    vpcId !== (saved.vpcId || '') ||
    subnetId !== (saved.subnetId || '') ||
    sgId !== (saved.securityGroupId || '')

  return (
    <div className="flex flex-col h-full">
      <Topbar
        title="Config"
        actions={
          <div className="flex gap-2">
            {(saved.region || saved.vpcId || saved.subnetId || saved.securityGroupId) && (
              <button
                onClick={handleClear}
                disabled={saveDefaults.isPending}
                className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
              >
                Clear
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={!isDirty || saveDefaults.isPending || !subnetId || !sgId}
              className="px-4 py-2 text-sm bg-[#185FA5] text-white rounded-md hover:bg-[#14508a] disabled:opacity-50"
            >
              {saveDefaults.isPending ? 'Saving…' : 'Save Defaults'}
            </button>
          </div>
        }
      />

      <div className="flex-1 overflow-y-auto p-6 space-y-5 max-w-3xl">
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg p-5 space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-1">
              Default AWS infrastructure
            </h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              These values are applied automatically when launching machines from any
              of the built-in templates that don't already specify a subnet and
              security group. Templates you've created from the UI keep their own
              values.
            </p>
          </div>

          <Field label="Region">
            <select
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              disabled={regionsLoading}
              className={inputCls}
            >
              {regions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label} ({r.id})
                </option>
              ))}
            </select>
          </Field>

          {discoveryError && (
            <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-md p-3 text-xs text-red-700 dark:text-red-300">
              <div className="font-medium mb-1">
                Could not load infrastructure for {region}
              </div>
              <div className="text-red-600 dark:text-red-400 mb-2 font-mono break-words">
                {discoveryError.message}
              </div>
              <button
                onClick={refetchDiscovery}
                className="underline text-red-700 dark:text-red-300 hover:text-red-900 dark:hover:text-red-100"
              >
                Retry
              </button>
            </div>
          )}

          <Field label="VPC">
            <select
              value={vpcId}
              onChange={(e) => setVpcId(e.target.value)}
              disabled={discoveryLoading || !vpcs.length}
              className={inputCls}
            >
              {!vpcs.length && (
                <option value="">
                  {discoveryLoading ? 'Loading…' : '— No VPCs available —'}
                </option>
              )}
              {vpcs.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.is_default ? '★ ' : ''}
                  {v.name || v.id} ({v.cidr})
                </option>
              ))}
            </select>
          </Field>

          <Field label="Subnet">
            <select
              value={subnetId}
              onChange={(e) => setSubnetId(e.target.value)}
              disabled={!subnetsForVpc.length}
              className={inputCls}
            >
              {!subnetsForVpc.length && (
                <option value="">
                  {discoveryLoading ? 'Loading…' : '— No subnets in this VPC —'}
                </option>
              )}
              {subnetsForVpc.map((s) => {
                const friendly =
                  s.name ||
                  `${s.availability_zone}${s.public ? ' (public)' : ''}`
                return (
                  <option key={s.id} value={s.id}>
                    {friendly} — {s.id}
                  </option>
                )
              })}
            </select>
          </Field>

          <Field label="Security Group">
            <select
              value={sgId}
              onChange={(e) => setSgId(e.target.value)}
              disabled={!sgsForVpc.length}
              className={inputCls}
            >
              {!sgsForVpc.length && (
                <option value="">
                  {discoveryLoading ? 'Loading…' : '— No security groups in this VPC —'}
                </option>
              )}
              {sgsForVpc.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name || 'unnamed'} — {g.id}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {(saved.region || saved.vpcId || saved.subnetId || saved.securityGroupId) && (
          <div className="bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 text-xs text-gray-600 dark:text-gray-400">
            <div className="font-semibold text-gray-700 dark:text-gray-300 mb-2">Currently saved</div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono">
              <div className="text-gray-500 dark:text-gray-400">region</div>
              <div className="text-gray-800 dark:text-gray-200">{saved.region || '—'}</div>
              <div className="text-gray-500 dark:text-gray-400">vpcId</div>
              <div className="text-gray-800 dark:text-gray-200">{saved.vpcId || '—'}</div>
              <div className="text-gray-500 dark:text-gray-400">subnetId</div>
              <div className="text-gray-800 dark:text-gray-200">{saved.subnetId || '—'}</div>
              <div className="text-gray-500 dark:text-gray-400">securityGroupId</div>
              <div className="text-gray-800 dark:text-gray-200">{saved.securityGroupId || '—'}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
