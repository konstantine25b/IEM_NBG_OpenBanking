import { useEffect, useState } from 'react'

export const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Delay used to pace the demo: the servers answer in well under a second, which is too fast to follow.
export const pace = (ms) => (reducedMotion() ? 0 : ms)

export const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// True shortly after mount, so CSS transitions have a start state to move away from.
export function useMounted(delay = 60) {
  const [mounted, setMounted] = useState(false)
  useEffect(() => {
    const id = setTimeout(() => setMounted(true), delay)
    return () => clearTimeout(id)
  }, [delay])
  return mounted
}

// Counts from 0 to `target` with an ease-out curve (instantly, with reduced motion).
export function useCountUp(target, duration = 1400) {
  const [value, setValue] = useState(reducedMotion() ? target : 0)
  useEffect(() => {
    const span = reducedMotion() ? 1 : duration
    let frame
    const start = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - start) / span)
      setValue(target * (1 - (1 - t) ** 3))
      if (t < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [target, duration])
  return value
}
