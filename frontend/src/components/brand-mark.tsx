export function BrandMark({ className = '' }: { className?: string }) {
  return <svg className={`brand-symbol ${className}`} viewBox="0 0 32 32" width="24" height="24" aria-hidden="true" focusable="false">
    <use href="/insightpulse.svg#mark"/>
  </svg>
}

export function BrandSignature() {
  return <>
    <BrandMark className="brand-mark"/>
    <svg className="brand-wordmark" viewBox="0 0 870 250" width="172" height="50" aria-hidden="true" focusable="false"><use href="/insightpulse-wordmark.svg#wordmark"/></svg>
  </>
}
