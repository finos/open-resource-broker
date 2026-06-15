import { NavLink } from 'react-router-dom'
import { useSystemHealth, useSystemInfo } from '../hooks'
import StatusDot from './StatusDot'

const NAV = [
  { to: '/',          label: 'Dashboard', icon: '▦' },
  { to: '/templates', label: 'Templates',  icon: '◧' },
  { to: '/machines',  label: 'Machines',   icon: '⬡' },
  { to: '/requests',  label: 'Requests',   icon: '↻' },
  { to: '/config',    label: 'Config',     icon: '⚙' },
]

/**
 * The ORB icon mark only (circular node-graph), extracted from the full SVG.
 * - Two outer arc paths use currentColor → dark slate in light mode, white in dark
 * - Inner node-graph lines are hardcoded #0f62fe (brand blue, same in both themes)
 */
function OrbIconMark({ className = '' }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 105 105.17"
      aria-hidden="true"
      className={className}
    >
      {/* Bottom arc */}
      <path
        fill="currentColor"
        d="M38.9615,103.378c-3.9241-1.051-7.5211-3.127-10.402-6.004-3.4161-3.416-5.6502-7.731-6.4651-12.481-.5764-3.331-.4239-6.677.4524-9.946,2.1406-7.984,8.4303-14.275,16.4152-16.4152,3.265-.8749,6.6056-1.0283,9.929-.4564,1.3996.2388,2.7871.6103,4.1276,1.1049,3.1626,1.1681,5.9776,2.9716,8.3662,5.3606,6.9652,6.965,17.2022,9.708,26.7162,7.156,3.884-1.039,7.422-2.87,10.5161-5.441.646-.536,1.279-1.114,1.882-1.716,1.668-1.6683,3.11-3.5391,4.296-5.5591-.847,6.924-3.09,13.673-6.683,19.896-7.023,12.164-18.363,20.865-31.93,24.501-8.9035,2.385-18.3162,2.385-27.2205,0h0Z"
      />
      {/* Top arc */}
      <path
        fill="currentColor"
        d="M89.7549,15.4021c6.518,6.5181,11.224,14.6692,13.61,23.5722,1.053,3.9302,1.053,8.0849,0,12.0154-1.052,3.9296-3.129,7.5283-6.008,10.4075-.506.5055-1.037.9896-1.579,1.4392-2.598,2.1596-5.567,3.6956-8.827,4.5686-7.986,2.141-16.577-.161-22.424-6.007-2.3895-2.3898-4.1921-5.204-5.3568-8.3643-.493-1.3308-.8636-2.7132-1.1028-4.1123-.5767-3.3325-.4243-6.6773.4522-9.9467,1.2551-4.6823,1.2551-9.6327,0-14.3163-1.2536-4.682-3.7288-8.9694-7.1581-12.3987-2.8438-2.8438-6.1976-4.9924-9.9679-6.3857-.7995-.296-1.6188-.5566-2.4341-.7739-2.2739-.61-4.6128-.9235-6.9546-.9401C38.4814,1.4079,45.5114-.0247,52.7558.0003c13.9761.0485,27.1151,5.5181,36.9991,15.4018h0Z"
      />
      {/* Node-graph lines — brand blue, unchanged in both themes */}
      <path
        fill="#0f62fe"
        d="M15.3888,15.4021c.0914-.0915.1837-.1819.277-.2732,2.7748-2.6854,6.2099-4.6508,9.937-5.6842l.2026-.054c2.2364-.5978,4.5425-.8156,6.8391-.7327-.1627.1153-.3481.1823-.4938.328-1.1829,1.1825-1.3023,2.9589-.483,4.3325l-5.1194,6.7936c-1.834-1.1058-4.2454-.8816-5.8278.7008-1.8628,1.8629-1.8631,4.8832-.0001,6.7462,1.8629,1.8628,4.8832,1.863,6.7461.0001,1.4961-1.4961,1.7693-3.7301.8629-5.5203l5.3765-7.1348c.0869.0246.1757.0334.2643.0515l1.8479,13.1483c-.8536.1906-1.6674.6068-2.332,1.2714-1.8627,1.8628-1.8627,4.8831,0,6.7462,1.863,1.8629,4.8835,1.8628,6.7463,0,1.8631-1.8629,1.8632-4.8835.0002-6.7465-.52-.52-1.1345-.8795-1.7842-1.1092l-1.9336-13.7581c.2375-.1451.4902-.2566.6956-.462,1.2994-1.2994,1.3373-3.3239.2201-4.7283.1258.0314.2551.042.3804.0756.6844.1826,1.3702.401,2.0405.6489,3.1658,1.17,5.9805,2.9733,8.3675,5.3604,2.8781,2.8781,4.9555,6.4763,6.0078,10.406,1.0536,3.9314,1.0535,8.0861,0,12.0153-.6687,2.4944-1.7311,4.8149-3.1557,6.9322.2115-1.3968-.2061-2.871-1.2817-3.9466-1.7974-1.7974-4.7117-1.7976-6.5093,0s-1.7973,4.7117.0001,6.5092c1.2878,1.2877,3.1451,1.6364,4.7581,1.0788-.454.4445-.9186.8761-1.3948,1.2698-2.5995,2.1609-5.5694,3.6977-8.8319,4.5722-4.7567,1.2751-9.0086,3.7859-12.3901,7.1674-3.1762,3.1764-5.5432,7.1394-6.8804,11.5484.0106-.252.0896-.489.0577-.747-.3131-2.55-2.6336-4.363-5.1834-4.05-.3168.039-.6221.109-.9137.207l-3.8786-5.5396c.2598-.4965.4225-1.0486.3491-1.6469-.1927-1.5692-1.6209-2.685-3.1897-2.4924-1.569.1927-2.6847,1.6208-2.4921,3.1897.1927,1.5692,1.6208,2.6852,3.1898,2.4922.021-.003.0366-.016.0574-.019l3.8459,5.492c-.7723.948-1.1783,2.191-1.0175,3.5.3132,2.55,2.6337,4.363,5.1832,4.05,1.5675-.193,2.782-1.186,3.4698-2.482-.7978,3.542-.9257,7.146-.3042,10.738.5456,3.181,1.6258,6.199,3.1896,8.954-5.5659-4.194-10.2828-9.507-13.8721-15.724C.2136,67.067-1.6972,53.3683,1.5222,40.1642c.0726,1.7386,1.1076,3.3674,2.8162,4.0927,1.3102.5562,2.7238.4466,3.9027-.1472l5.9781,5.773c-.3933.4378-.7354.9385-.9809,1.517-1.1116,2.6186-.0466,5.5757,2.3787,6.6051,2.425,1.0294,5.2919-.2588,6.4036-2.877.3468-.8174.4593-1.6635.4068-2.4777l5.0935-1.7539c.2234.1959.4551.387.7434.5094,1.4553.6178,3.1354-.0614,3.7532-1.5161.6176-1.4551-.0613-3.1354-1.5165-3.7531-1.455-.6177-3.1353.0612-3.7532,1.516-.1159.274-.1437.5573-.1717.8382l-4.955,1.7063c-.4795-.7224-1.1413-1.3182-1.9792-1.6739-1.0488-.4454-2.174-.4351-3.2104-.0935l-6.2985-6.0825c.1071-.1799.2197-.3556.304-.5539,1.0036-2.3644-.0995-5.095-2.4639-6.0987-2.1412-.9088-4.5686-.0786-5.7561,1.8396,2.4929-8.3374,7.0091-15.9693,13.1718-22.1319h0ZM46.9335,23.7606c.9781-.9781.9779-2.5638,0-3.5419-.978-.9781-2.5637-.978-3.5418,0-.9781.9781-.978,2.5637,0,3.5417.978.978,2.5638.9783,3.5419.0002h0ZM18.1611,37.0422c-.9781.9781-.978,2.564-.0001,3.5419.9781.9781,2.5639.9781,3.5419,0,.978-.9779.978-2.5637,0-3.5418-.9779-.9779-2.5638-.978-3.5417-.0001h0Z"
      />
    </svg>
  )
}

export default function Sidebar() {
  const { data: health } = useSystemHealth()
  const { data: info } = useSystemInfo()

  const healthStatus =
    health?.status === 'ok' || health?.status === 'healthy' ? 'healthy' :
    health ? 'degraded' : 'unknown'

  return (
    <aside className="w-[190px] min-h-screen bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 flex flex-col shrink-0">

      {/* ── Logo banner ─────────────────────────────────────────────────
          Layout:

           [ icon ]  Open
                     Resource
                     Broker

          Icon is vertically centred against the three text lines.
          Leading O, R, B are in brand blue (#0f62fe); rest of each word
          follows the sidebar foreground → auto-adapts to light/dark.
      ───────────────────────────────────────────────────────────────── */}
      <div className="px-4 py-5 border-b border-gray-100 dark:border-gray-700 flex items-center gap-3 select-none">
        {/* Icon mark */}
        <div className="text-gray-900 dark:text-white w-14 h-14 shrink-0">
          <OrbIconMark className="w-full h-full" />
        </div>

        {/* Three-line wordmark — font larger than nav items (text-sm/14px) */}
        <div className="flex flex-col leading-tight">
          <span className="text-[15px] font-semibold tracking-wide text-gray-800 dark:text-gray-100">
            <span style={{ color: '#0f62fe' }}>O</span>pen
          </span>
          <span className="text-[15px] font-semibold tracking-wide text-gray-800 dark:text-gray-100">
            <span style={{ color: '#0f62fe' }}>R</span>esource
          </span>
          <span className="text-[15px] font-semibold tracking-wide text-gray-800 dark:text-gray-100">
            <span style={{ color: '#0f62fe' }}>B</span>roker
          </span>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3">
        {NAV.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors relative
               ${isActive
                ? 'text-[#185FA5] font-medium bg-blue-50 dark:bg-blue-950 border-l-2 border-[#185FA5]'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800 border-l-2 border-transparent'
               }`
            }
          >
            <span className="text-base leading-none">{icon}</span>
            {label}
          </NavLink>
        ))}
      </nav>

      {/* System health + info */}
      <div className="px-4 py-3 border-t border-gray-100 dark:border-gray-700 space-y-1.5">
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
          <StatusDot status={healthStatus} size="sm" />
          <span>
            {healthStatus === 'healthy' ? 'Server online' :
             healthStatus === 'degraded' ? 'Degraded' : 'Server offline'}
          </span>
        </div>
        {info && (
          <div className="text-[10px] text-gray-400 dark:text-gray-500 leading-tight">
            <div className="mono">v{info.version}</div>
            <div>auth: {info.auth_enabled ? info.auth_strategy : 'off'}</div>
          </div>
        )}
      </div>
    </aside>
  )
}
