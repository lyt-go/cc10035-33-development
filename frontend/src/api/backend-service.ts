import { fetchJson, request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type ReconciliationState = {
  ok?: boolean
  scenario?: string
  samples: Array<Record<string, any>>
  disputes: Array<Record<string, any>>
  windows: Array<Record<string, any>>
  events?: Array<Record<string, any>>
  summary: Record<string, number>
  [key: string]: unknown
}

async function postJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return await response.json() as T
}

export function getReconciliationState(): Promise<ReconciliationState> {
  return fetchJson<ReconciliationState>('/api/ts_point/reconciliation/state')
}

export function resetReconciliation(scenario = 'conflict'): Promise<ReconciliationState> {
  return postJson('/api/ts_point/scenario/reset', { scenario })
}

export function reportSample(values: Record<string, unknown>): Promise<Record<string, any>> {
  return postJson('/api/ts_point/samples', values)
}

export function readReconciliationWindow(values: Record<string, unknown>): Promise<Record<string, any>> {
  return postJson('/api/ts_point/windows/read', values)
}

export function adjudicateDispute(
  disputeId: string,
  values: Record<string, unknown>,
): Promise<Record<string, any>> {
  return postJson(`/api/ts_point/disputes/${encodeURIComponent(disputeId)}/adjudicate`, values)
}

export function continueReconciliationWindow(
  windowId: string,
  values: Record<string, unknown> = {},
): Promise<Record<string, any>> {
  return postJson(`/api/ts_point/windows/${encodeURIComponent(windowId)}/continue`, values)
}

export async function reopenDispute(disputeId: string): Promise<Record<string, any>> {
  return postJson(`/api/ts_point/disputes/${encodeURIComponent(disputeId)}/reopen`, {})
}

export async function getDisputeDetail(disputeId: string): Promise<Record<string, any>> {
  return fetchJson<Record<string, any>>(`/api/ts_point/disputes/${encodeURIComponent(disputeId)}`)
}
