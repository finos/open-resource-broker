const DOT_STYLES = {
  healthy:   'bg-green-500',
  degraded:  'bg-amber-500',
  unhealthy: 'bg-red-500',
  running:   'bg-green-500',
  complete:  'bg-green-500',
  completed: 'bg-green-500',
  launched:  'bg-green-500',
  succeed:   'bg-green-500',
  success:   'bg-green-500',
  stopped:   'bg-gray-400',
  'shutting-down': 'bg-gray-400',
  terminated:'bg-gray-400',
  cancelled: 'bg-gray-400',
  pending:   'bg-amber-500',
  in_progress: 'bg-amber-500',
  partial:   'bg-amber-500',
  failed:    'bg-red-500',
  fail:      'bg-red-500',
  timeout:   'bg-red-500',
  error:     'bg-red-500',
  unknown:   'bg-gray-400',
}

export default function StatusDot({ status, size = 'md' }) {
  const key = String(status || '').toLowerCase().trim()
  const color = DOT_STYLES[key] || 'bg-gray-400'
  const sz = size === 'sm' ? 'w-2 h-2' : 'w-2.5 h-2.5'
  return <span className={`inline-block rounded-full ${sz} ${color}`} />
}
