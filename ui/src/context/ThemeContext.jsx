import { createContext, useContext, useEffect, useState } from 'react'

const ThemeCtx = createContext(null)

/**
 * Manages light/dark theme preference.
 *
 * Resolution order:
 *  1. localStorage ('orb-theme': 'light' | 'dark')
 *  2. Browser's prefers-color-scheme media query
 *
 * Applies the `dark` class to <html> so Tailwind's `dark:` variants work.
 */
export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => {
    // Hydrate from storage, falling back to the OS preference
    const stored = localStorage.getItem('orb-theme')
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  // Keep <html class="dark"> in sync whenever theme changes
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('orb-theme', theme)
  }, [theme])

  // Also listen for OS-level changes when the user hasn't explicitly chosen
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e) => {
      // Only follow OS changes when no explicit preference has been stored
      if (!localStorage.getItem('orb-theme')) {
        setThemeState(e.matches ? 'dark' : 'light')
      }
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  const toggleTheme = () =>
    setThemeState((t) => (t === 'dark' ? 'light' : 'dark'))

  return (
    <ThemeCtx.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeCtx.Provider>
  )
}

export const useTheme = () => {
  const ctx = useContext(ThemeCtx)
  if (!ctx) throw new Error('useTheme must be inside ThemeProvider')
  return ctx
}
