// prefers-reduced-motion helpers (vestibular safety: no smooth scrolling, no animated graph layout).
export const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
export const scrollBehavior = () => (reducedMotion() ? 'auto' : 'smooth');
