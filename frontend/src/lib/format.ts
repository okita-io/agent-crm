export function formatTokenCount(count: number): string {
  const value = Math.max(0, count)
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 10_000) return `${(value / 1000).toFixed(1)}k`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return value.toLocaleString()
}

export function formatTokenRate(rate: number): string {
  if (rate <= 0) return "—"
  if (rate >= 1_000_000) return `${(rate / 1_000_000).toFixed(1)}M/hr`
  if (rate >= 10_000) return `${(rate / 1000).toFixed(1)}k/hr`
  return `${Math.round(rate).toLocaleString()}/hr`
}

export function formatUsd(amount: number): string {
  if (amount <= 0) return "$0.00"
  if (amount < 0.01) return `$${amount.toFixed(4)}`
  return `$${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function padSlotIndex(index: number): string {
  return String(index + 1).padStart(2, "0")
}
