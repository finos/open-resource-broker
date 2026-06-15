import { useEffect, useMemo, useState } from 'react'
import { useQueryClient, useMutation } from '@tanstack/react-query'
import client from '../api/client'
import { syncAwsTemplate } from '../api/aws'
import { useAwsDiscovery, useValidateTemplate } from '../hooks'
import { useToast } from '../components/Toast'

// ── Static option lists ────────────────────────────────────────────────────────

const PROVIDER_APIS = [
  { value: 'aws', label: 'AWS' },
  { value: 'RunInstances', label: 'AWS · RunInstances' },
  { value: 'EC2Fleet', label: 'AWS · EC2Fleet' },
  { value: 'SpotFleet', label: 'AWS · SpotFleet' },
  { value: 'ASG', label: 'AWS · Auto Scaling Group' },
]

const COMMON_INSTANCE_TYPES = [
  't3.nano',
  't3.micro',
  't3.small',
  't3.medium',
  't3.large',
  't3.xlarge',
  't4g.micro',
  't4g.small',
  't4g.medium',
  'm5.large',
  'm5.xlarge',
  'm6i.large',
  'm6i.xlarge',
  'c5.large',
  'c5.xlarge',
  'c6i.large',
  'r5.large',
]

// ── Helpers ────────────────────────────────────────────────────────────────────

const randSuffix = () =>
  Math.random().toString(36).slice(2, 8).replace(/[^a-z0-9]/g, '')

// Generates a stable-ish ID like `aws-t3-micro-x9k2vp`
const generateTemplateId = (providerApi, instanceType) => {
  const safe = (s) =>
    String(s || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
  return [safe(providerApi), safe(instanceType), randSuffix()].filter(Boolean).join('-')
}

const parseTags = (s) => {
  const out = {}
  s.split(/[,\n]/)
    .map((p) => p.trim())
    .filter(Boolean)
    .forEach((p) => {
      const [k, ...rest] = p.split('=')
      if (k) out[k.trim()] = rest.join('=').trim()
    })
  return out
}

// ── Mutation ───────────────────────────────────────────────────────────────────

const useCreateTemplate = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body) => {
      const { data } = await client.post('/templates/', body)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }),
  })
}

// ── Layout primitives ──────────────────────────────────────────────────────────

const inputCls =
  'w-full border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-[#185FA5]'

function Section({ title, children }) {
  return (
    <div>
      <h3 className="text-xs uppercase tracking-wider font-semibold text-gray-500 dark:text-gray-400 mb-2">
        {title}
      </h3>
      <div className="space-y-3">{children}</div>
    </div>
  )
}

function Field({ label, required, children, hint }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
      {hint && <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-0.5">{hint}</p>}
    </div>
  )
}

// ── Modal ──────────────────────────────────────────────────────────────────────

export default function CreateTemplateModal({ onClose }) {
  const { show } = useToast()
  const createTemplate = useCreateTemplate()
  const region = 'us-east-1'

  const {
    data: discovery,
    isLoading: discoveryLoading,
    error: discoveryError,
    refetch: refetchDiscovery,
  } = useAwsDiscovery(region)

  const vpcs = discovery?.vpcs || []
  const subnets = discovery?.subnets || []
  const securityGroups = discovery?.security_groups || []
  const amis = discovery?.amis || []

  const defaultVpcId = useMemo(() => {
    const def = vpcs.find((v) => v.is_default)
    return def?.id || vpcs[0]?.id || ''
  }, [vpcs])

  const [vpcId, setVpcId] = useState('')

  // Selected IDs and free-text fields. Mirrors the documented POST schema.
  const [form, setForm] = useState({
    templateId: '',
    templateIdOverridden: false,
    name: '',
    description: '',
    providerApi: 'aws',
    imageId: '',
    instanceType: 't3.micro',
    keyName: '',
    subnetIds: [],
    securityGroupIds: [],
    userData: '',
    tagsText: 'Environment=dev, ManagedBy=orb',
    version: '1.0',
  })

  const update = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  // Initialise VPC + AMI defaults once discovery returns
  useEffect(() => {
    if (defaultVpcId && !vpcId) setVpcId(defaultVpcId)
  }, [defaultVpcId, vpcId])

  useEffect(() => {
    if (!form.imageId && amis[0]) update('imageId', amis[0].id)
  }, [amis]) // eslint-disable-line react-hooks/exhaustive-deps

  const subnetsForVpc = useMemo(
    () => subnets.filter((s) => !vpcId || s.vpc_id === vpcId),
    [subnets, vpcId]
  )
  const sgsForVpc = useMemo(
    () => securityGroups.filter((g) => !vpcId || g.vpc_id === vpcId),
    [securityGroups, vpcId]
  )

  // Reset subnet/SG selection when VPC changes (default to first subnet, "default" SG)
  useEffect(() => {
    const firstSubnet = subnetsForVpc[0]?.id
    update('subnetIds', firstSubnet ? [firstSubnet] : [])
  }, [subnetsForVpc]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const def = sgsForVpc.find((g) => g.name === 'default') || sgsForVpc[0]
    update('securityGroupIds', def ? [def.id] : [])
  }, [sgsForVpc]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-generate templateId from provider + instance type unless user overrode
  useEffect(() => {
    if (form.templateIdOverridden) return
    update('templateId', generateTemplateId(form.providerApi, form.instanceType))
  }, [form.providerApi, form.instanceType, form.templateIdOverridden])

  const valid =
    form.templateId.trim() &&
    form.imageId.trim() &&
    form.instanceType.trim() &&
    form.subnetIds.length > 0 &&
    form.securityGroupIds.length > 0

  const buildBody = () => {
    // Body matches the documented POST /api/v1/templates schema (camelCase keys).
    const body = {
      templateId: form.templateId.trim(),
      name: form.name.trim() || form.templateId.trim(),
      description: form.description.trim() || undefined,
      providerApi: form.providerApi,
      imageId: form.imageId.trim(),
      instanceType: form.instanceType.trim(),
      keyName: form.keyName.trim() || undefined,
      securityGroupIds: form.securityGroupIds,
      subnetIds: form.subnetIds,
      userData: form.userData || undefined,
      tags: { ...parseTags(form.tagsText), CreatedBy: 'orb-ui' },
      version: form.version || '1.0',
    }
    Object.keys(body).forEach((k) => body[k] === undefined && delete body[k])
    return body
  }

  const validateTemplate = useValidateTemplate()

  const handleValidate = async () => {
    try {
      const result = await validateTemplate.mutateAsync(buildBody())
      const errors = result?.validation_errors || result?.validationErrors || []
      const isValid = result?.valid !== false && errors.length === 0
      if (isValid) {
        show({ type: 'success', message: 'Template is valid' })
      } else {
        const summary = Array.isArray(errors)
          ? errors.map((e) => (typeof e === 'string' ? e : JSON.stringify(e))).join('; ')
          : String(errors)
        show({ type: 'error', message: `Validation failed: ${summary}` })
      }
    } catch (err) {
      show({ type: 'error', message: err.message || 'Validation request failed' })
    }
  }

  const handleSubmit = async () => {
    if (!valid) return
    try {
      const body = buildBody()
      await createTemplate.mutateAsync(body)

      // Sync to AWS as a best-effort operation — ORB template was saved regardless
      try {
        const sync = await syncAwsTemplate(body)
        if (sync?.action === 'created') {
          show({
            type: 'success',
            message: `Template "${body.templateId}" created (AWS Launch Template: ${sync.launchTemplateId})`,
          })
        } else if (sync?.errors?.length) {
          show({
            type: 'success',
            message: `Template "${body.templateId}" created (AWS sync failed: ${sync.errors[0]?.reason || 'unknown'})`,
          })
        } else {
          show({ type: 'success', message: `Template "${body.templateId}" created` })
        }
      } catch (syncErr) {
        show({
          type: 'success',
          message: `Template "${body.templateId}" created (AWS sync failed: ${syncErr.message || 'unknown'})`,
        })
      }

      onClose()
    } catch (err) {
      show({ type: 'error', message: err.message || 'Failed to create template' })
    }
  }

  const regenId = () =>
    setForm((f) => ({
      ...f,
      templateId: generateTemplateId(f.providerApi, f.instanceType),
      templateIdOverridden: false,
    }))

  // ── Discovery status banner ────────────────────────────────────────────────

  const renderDiscoveryStatus = () => {
    if (discoveryLoading) {
      return (
        <div className="bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-md p-3 text-xs text-gray-500 dark:text-gray-400">
          Loading AWS infrastructure ({region})…
        </div>
      )
    }
    if (discoveryError) {
      return (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-md p-3 text-xs text-red-700 dark:text-red-300">
          <div className="font-medium mb-1">Could not load AWS infrastructure</div>
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
      )
    }
    return (
      <div className="bg-blue-50 dark:bg-blue-950/50 border border-blue-100 dark:border-blue-800 rounded-md p-3 text-xs text-gray-600 dark:text-gray-400">
        Region <span className="font-mono">{discovery?.region}</span> · {vpcs.length}
        {' '}VPCs · {subnets.length} subnets · {securityGroups.length} SGs · {amis.length}
        {' '}AMIs
      </div>
    )
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  const subnetLabel = (s) => {
    const friendly = s.name || `${s.availability_zone}${s.public ? ' (public)' : ''}`
    return `${friendly} — ${s.id}`
  }

  const sgLabel = (g) => `${g.name || 'unnamed'} — ${g.id}`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 dark:bg-black/60">
      <div
        className="bg-white dark:bg-gray-900 rounded-lg shadow-xl w-full max-w-2xl mx-4 my-6 max-h-[90vh] flex flex-col border border-transparent dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pt-5 pb-3 border-b border-gray-100 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Add Template</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {renderDiscoveryStatus()}

          <Section title="Identity">
            <Field label="Name">
              <input
                value={form.name}
                onChange={(e) => update('name', e.target.value)}
                placeholder="e.g. Spot fleet for batch jobs"
                className={inputCls}
              />
            </Field>

            <Field label="Description">
              <input
                value={form.description}
                onChange={(e) => update('description', e.target.value)}
                placeholder="Optional"
                className={inputCls}
              />
            </Field>

            <Field
              label="Template ID"
              required
              hint="Auto-generated from provider + instance type. Edit to override."
            >
              <div className="flex gap-2">
                <input
                  value={form.templateId}
                  onChange={(e) => {
                    update('templateId', e.target.value)
                    update('templateIdOverridden', true)
                  }}
                  className={`${inputCls} font-mono`}
                />
                <button
                  type="button"
                  onClick={regenId}
                  className="px-3 py-2 text-xs border border-gray-300 rounded-md text-gray-600 hover:bg-gray-50 whitespace-nowrap"
                >
                  ↻ Regenerate
                </button>
              </div>
            </Field>
          </Section>

          <Section title="Compute">
            <Field label="Provider API" required>
              <select
                value={form.providerApi}
                onChange={(e) => update('providerApi', e.target.value)}
                className={inputCls}
              >
                {PROVIDER_APIS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Instance Type" required>
              <input
                list="instance-types"
                value={form.instanceType}
                onChange={(e) => update('instanceType', e.target.value)}
                placeholder="t3.micro"
                className={`${inputCls} font-mono`}
              />
              <datalist id="instance-types">
                {COMMON_INSTANCE_TYPES.map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </Field>

            <Field label="Key Name" hint="Optional EC2 SSH key pair">
              <input
                value={form.keyName}
                onChange={(e) => update('keyName', e.target.value)}
                placeholder="my-keypair"
                className={`${inputCls} font-mono`}
              />
            </Field>
          </Section>

          <Section title="AWS infrastructure">
            <Field label="AMI" required>
              <select
                value={form.imageId}
                onChange={(e) => update('imageId', e.target.value)}
                disabled={!amis.length}
                className={`${inputCls} font-mono`}
              >
                {!amis.length && <option value="">— No AMIs available —</option>}
                {amis.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.label} — {a.id}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="VPC">
              <select
                value={vpcId}
                onChange={(e) => setVpcId(e.target.value)}
                disabled={!vpcs.length}
                className={inputCls}
              >
                {!vpcs.length && <option value="">— No VPCs available —</option>}
                {vpcs.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.is_default ? '★ ' : ''}
                    {v.name || v.id} ({v.cidr})
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Subnet" required>
              <select
                value={form.subnetIds[0] || ''}
                onChange={(e) =>
                  update('subnetIds', e.target.value ? [e.target.value] : [])
                }
                disabled={!subnetsForVpc.length}
                className={inputCls}
              >
                {!subnetsForVpc.length && (
                  <option value="">— No subnets in this VPC —</option>
                )}
                {subnetsForVpc.map((s) => (
                  <option key={s.id} value={s.id}>
                    {subnetLabel(s)}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Security Group" required>
              <select
                value={form.securityGroupIds[0] || ''}
                onChange={(e) =>
                  update('securityGroupIds', e.target.value ? [e.target.value] : [])
                }
                disabled={!sgsForVpc.length}
                className={inputCls}
              >
                {!sgsForVpc.length && (
                  <option value="">— No security groups in this VPC —</option>
                )}
                {sgsForVpc.map((g) => (
                  <option key={g.id} value={g.id}>
                    {sgLabel(g)}
                  </option>
                ))}
              </select>
            </Field>
          </Section>

          <Section title="User data">
            <Field label="User data (cloud-init / bash)">
              <textarea
                value={form.userData}
                onChange={(e) => update('userData', e.target.value)}
                rows={3}
                placeholder="#!/bin/bash..."
                className={`${inputCls} font-mono text-xs`}
              />
            </Field>
          </Section>

          <Section title="Tags">
            <Field label="Tags" hint="key=value pairs separated by comma or newline">
              <textarea
                value={form.tagsText}
                onChange={(e) => update('tagsText', e.target.value)}
                rows={2}
                className={`${inputCls} font-mono text-xs`}
              />
            </Field>
          </Section>
        </div>

        <div className="flex items-center justify-between gap-3 px-6 py-4 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 rounded-b-lg">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {valid ? (
              <>
                Will create <span className="font-mono text-gray-700 dark:text-gray-300">{form.templateId}</span>
              </>
            ) : (
              'Fill required fields to continue'
            )}
          </p>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:bg-white dark:hover:bg-gray-700"
            >
              Cancel
            </button>
            <button
              onClick={handleValidate}
              disabled={!valid || validateTemplate.isPending}
              className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:bg-white dark:hover:bg-gray-700 disabled:opacity-50"
            >
              {validateTemplate.isPending ? 'Validating…' : 'Validate'}
            </button>
            <button
              onClick={handleSubmit}
              disabled={!valid || createTemplate.isPending}
              className="px-5 py-2 text-sm bg-[#185FA5] text-white rounded-md font-medium hover:bg-[#14508a] disabled:opacity-50"
            >
              {createTemplate.isPending ? 'Creating…' : 'Create Template'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
