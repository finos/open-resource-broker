export default function ErrorBanner({ error, onRetry }) {
  const isNetworkError = !error?.status

  if (isNetworkError) {
    const base = import.meta.env.VITE_ORB_API_BASE || 'http://localhost:8000/api/v1'
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center px-6">
        <div className="text-5xl mb-4">⚡</div>
        <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-200 mb-2">Cannot connect to ORB server</h2>
        <p className="text-gray-500 dark:text-gray-400 mb-2 text-sm">
          Configured API URL: <span className="mono text-gray-700 dark:text-gray-300">{base}</span>
        </p>
        <p className="text-gray-500 dark:text-gray-400 mb-6 text-sm">
          Make sure the server is running and try again.
        </p>
        <div className="bg-gray-900 dark:bg-gray-950 text-green-400 rounded-lg px-6 py-3 font-mono text-sm mb-6 border border-gray-700">
          orb system serve
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="px-4 py-2 text-sm bg-[#185FA5] text-white rounded-md hover:bg-[#14508a]"
          >
            Retry
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/30 p-4 text-sm text-red-700 dark:text-red-300">
      <strong>Error:</strong> {error?.message || 'Something went wrong'}
      {onRetry && (
        <button onClick={onRetry} className="ml-3 underline text-red-700 dark:text-red-300 hover:text-red-900 dark:hover:text-red-100">
          Retry
        </button>
      )}
    </div>
  )
}
