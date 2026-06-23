import { useEffect, useMemo, useState } from 'react'
import { useQueryClient, useMutation } from '@tanstack/react-query'
import client from '../api/client'
import { syncAwsTemplate } from '../api/aws'
import { useAwsDiscovery, useValidateTemplate } from '../hooks'
import { useToast } from '../components/Toast'

const PROVIDER_APIS = [
  { value: 'aws', label: 'AWS' },
  { value: 'RunInstances', label: 'AWS · RunInstances' },
  { value: 'EC2Fleet', label: 'AWS · EC2Fleet' },
  { value: 'SpotFleet', label: 'AWS · SpotFleet' },
  { value: 'ASG', label: 'AWS · Auto Scaling Group' },
]

const COMMON_INSTANCE_TYPES = [
  't3.nano', 't3.micro', 't3.small', 't3.medium', 't3.large', 't3.xlarge',
  't4g.micro', 't4g.small', 't4g.medium',
  'm5.large', 'm5.xlarge', 'm6i.large', 'm6i.xlarge',
  'c5.large', 'c5.xlarge', 'c6i.large', 'r5.large',
]

const tagsToText = (tags) => {
  if (!tags || typeof tags !== 'object') return ''
  return Object.entries(tags)
    .filter(([k]) => k !== 'CreatedBy')
    .map(([k, v]) => `${k}=${v}`)
    .join(', ')
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

const useUpdateTemplate = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, body }) => {
      const { data } = await client.put(`/templates/${id}`, body)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }),
  })
}

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

export default function EditTemplateModal({ template, onClose }) {
  const { show } = useToast()
  const updateTemplate = useUpdateTemplate()
  const validateTemplate = useValidateTemplate()
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

  const initialSubnetId = template.subnet_ids?.[0] || ''
  const initialSgId = template.security_group_ids?.[0] || ''

  // Determine the VPC of the existing subnet so VPC dropdown reflects the template
  const initialVpcId = useMemo(() => {
    const s = subnets.find((s) => s.id === initialSubnetId)
    return s?.vpc_id || vpcs.find((v) => v.is_default)?.id || vpcs[0]?.id || ''
  }, [subnets, vpcs, initialSubnetId])

  const [vpcId, setVpcId] = useState('')
  useEffect(() => {
    if (initialVpcId && !vpcId) setVpcId(initialVpcId)
  }, [initialVpcId, vpcId])

  const [form, setForm] = useState({
    templateId: template.template_id || '',
    name: template.name || '',
    description: template.description || '',
    providerApi: template.provider_api || 'aws',
    imageId: template.image_id || '',
    instanceType: template.instance_type || '',
    keyName: template.key_name || '',
    subnetIds: template.subnet_ids?.length ? [template.subnet_ids[0]] : [],
    securityGroupIds: template.security_group_ids?.length ? [template.security_group_ids[0]] : [],
    userData: template.user_data || '',
    tagsText: tagsToText(template.tags),
    version: template.version || '1.0',
  })

  const update = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  const subnetsForVpc = useMemo(
    () => subnets.filter((s) => !vpcId || s.vpc_id === vpcId),
    [subnets, vpcId]
  )
  const sgsForVpc = useMemo(
    () => securityGroups.filter((g) => !vpcId || g.vpc_id === vpcId),
    [securityGroups, vpcId]
  )

  // If user changes VPC away from the original, reset subnet/SG to first valid
  useEffect(() => {
    if (!vpcId || vpcId === initialVpcId) return
    if (subnetsForVpc.length && !subnetsForVpc.find((s) => s.id === form.subnetIds[0])) {
      update('subnetIds', subnetsForVpc[0] ? [subnetsForVpc[0].id] : [])
    }
    if (sgsForVpc.length && !sgsForVpc.find((g) => g.id === form.securityGroupIds[0])) {
      const def = sgsForVpc.find((g) => g.name === 'default') || sgsForVpc[0]
      update('securityGroupIds', def ? [def.id] : [])
    }
  }, [vpcId]) // eslint-disable-line react-hooks/exhaustive-deps

  const valid =
    form.imageId.trim() &&
    form.instanceType.trim() &&
    form.subnetIds.length > 0 &&
    form.securityGroupIds.length > 0

  const buildBody = () => {
    const body = {
      name: form.name.trim() || form.templateId,
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

  const handleValidate = async () => {
    try {
      const body = { templateId: form.templateId, ...buildBody() }
      const result = await validateTemplate.mutateAsync(body)
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
      await updateTemplate.mutateAsync({ id: form.templateId, body })

      // Sync changes to AWS as a best-effort operation
      try {
        const syncPayload = { templateId: form.templateId, ...body }
        const sync = await syncAwsTemplate(syncPayload)
        if (sync?.action === 'updated') {
          show({
            type: 'success',
            message: `Template "${form.templateId}" updated (AWS Launch Template version ${sync.version})`,
          })
        } else if (sync?.action === 'created') {
          show({
            type: 'success',
            message: `Template "${form.templateId}" updated (created new AWS Launch Template: ${sync.launchTemplateId})`,
          })
        } else if (sync?.errors?.length) {
          show({
            type: 'success',
            message: `Template "${form.templateId}" updated (AWS sync failed: ${sync.errors[0]?.reason || 'unknown'})`,
          })
        } else {
          show({ type: 'success', message: `Template "${form.templateId}" updated` })
        }
      } catch (syncErr) {
        show({
          type: 'success',
          message: `Template "${form.templateId}" updated (AWS sync failed: ${syncErr.message || 'unknown'})`,
        })
      }

      onClose()
    } catch (err) {
      show({ type: 'error', message: err.message || 'Failed to update template' })
    }
  }

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
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Edit Template</h2>
            <p className="text-xs text-gray-400 dark:text-gray-500 font-mono mt-0.5">{form.templateId}</p>
          </div>
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
            <Field label="Template ID" hint="Cannot be changed">
              <input
                value={form.templateId}
                disabled
                className={`${inputCls} font-mono bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400`}
              />
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
                list="instance-types-edit"
                value={form.instanceType}
                onChange={(e) => update('instanceType', e.target.value)}
                placeholder="t3.micro"
                className={`${inputCls} font-mono`}
              />
              <datalist id="instance-types-edit">
                {COMMON_INSTANCE_TYPES.map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </Field>

            <Field label="Key Name" hint="Optional EC2 SSH key pair">
              <input
                value={form.keyName}
                onChange={(e) => update('keyName', e.target.value)}
                className={`${inputCls} font-mono`}
              />
            </Field>
          </Section>

          <Section title="AWS infrastructure">
            <Field label="AMI" required>
              <select
                value={form.imageId}
                onChange={(e) => update('imageId', e.target.value)}
                className={`${inputCls} font-mono`}
              >
                {!amis.find((a) => a.id === form.imageId) && form.imageId && (
                  <option value={form.imageId}>{form.imageId} (current)</option>
                )}
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
                {!subnetsForVpc.find((s) => s.id === form.subnetIds[0]) &&
                  form.subnetIds[0] && (
                    <option value={form.subnetIds[0]}>
                      {form.subnetIds[0]} (current, outside VPC)
                    </option>
                  )}
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
                {!sgsForVpc.find((g) => g.id === form.securityGroupIds[0]) &&
                  form.securityGroupIds[0] && (
                    <option value={form.securityGroupIds[0]}>
                      {form.securityGroupIds[0]} (current, outside VPC)
                    </option>
                  )}
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

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 rounded-b-lg">
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
            disabled={!valid || updateTemplate.isPending}
            className="px-5 py-2 text-sm bg-[#185FA5] text-white rounded-md font-medium hover:bg-[#14508a] disabled:opacity-50"
          >
            {updateTemplate.isPending ? 'Saving…' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  )
}
