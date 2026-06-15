const STATUS_STYLES = {
  running:     'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-700',
  complete:    'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-700',
  completed:   'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-700',
  launched:    'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-700',
  succeed:     'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-700',
  success:     'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-700',
  healthy:     'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-700',
  in_progress: 'bg-amber-100 text-amber-800 border-amber-200 badge-pulse dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  pending:     'bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  partial:     'bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
  failed:      'bg-red-100 text-red-800 border-red-200 dark:bg-red-900/40 dark:text-red-300 dark:border-red-700',
  fail:        'bg-red-100 text-red-800 border-red-200 dark:bg-red-900/40 dark:text-red-300 dark:border-red-700',
  timeout:     'bg-red-100 text-red-800 border-red-200 dark:bg-red-900/40 dark:text-red-300 dark:border-red-700',
  error:       'bg-red-100 text-red-800 border-red-200 dark:bg-red-900/40 dark:text-red-300 dark:border-red-700',
  unhealthy:   'bg-red-100 text-red-800 border-red-200 dark:bg-red-900/40 dark:text-red-300 dark:border-red-700',
  cancelled:   'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600',
  stopped:     'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600',
  'shutting-down': 'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600',
  terminated:  'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600',
  degraded:    'bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700',
}

const LABELS = {
  in_progress: 'In Progress',
  running:     'Running',
  complete:    'Complete',
  completed:   'Completed',
  launched:    'Launched',
  succeed:     'Running',
  success:     'Running',
  failed:      'Failed',
  fail:        'Failed',
  cancelled:   'Cancelled',
  pending:     'Pending',
  partial:     'Partial',
  timeout:     'Timeout',
  stopped:     'Stopped',
  'shutting-down': 'Shutting Down',
  terminated:  'Terminated',
  healthy:     'Healthy',
  degraded:    'Degraded',
  unhealthy:   'Unhealthy',
  error:       'Error',
}

export default function Badge({ status, className = '' }) {
  const key = String(status || '').toLowerCase().trim()
  const styles = STATUS_STYLES[key] || 'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600'
  const label = LABELS[key] || (status ? String(status) : '—')
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${styles} ${className}`}
    >
      {label}
    </span>
  )
}
