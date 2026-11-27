<template>
  <section class="recon-panel" data-testid="ts-reconciliation" aria-label="同位冲突读数与争议处理单">
    <div class="recon-content" :inert="Boolean(selectedDispute || windowDetail)">
    <div class="recon-head">
      <div>
        <h3>同位读数冲突 · 争议处理单</h3>
        <p>
          同一采样身份的相同值重送只算一次；另一渠道的异值保留来源并开单。裁定后该位置仅保留一项有效样本，
          服务端只重算依赖该位置且尚未签收的窗口。
        </p>
      </div>
      <div class="recon-demo-actions">
        <button class="btn primary" type="button" data-testid="reset-demo" @click="resetDemo('conflict')">
          重置验收场景
        </button>
        <button class="btn" type="button" data-testid="load-conflict-demo" @click="resetDemo('conflict')">
          载入完整冲突场景
        </button>
        <button class="btn ghost" type="button" data-testid="prepare-ready-demo" @click="resetDemo('ready')">
          准备：10 已送两次且窗口已读
        </button>
      </div>
    </div>

    <form class="recon-ingest" data-testid="reading-form" @submit.prevent="submitReport">
      <label>
        <span>测点 P</span>
        <input v-model="reportForm.point_code" data-testid="report-point" aria-label="测点编号" />
      </label>
      <label>
        <span>采样时间</span>
        <input v-model="reportForm.sample_time" data-testid="report-time" aria-label="采样时间" />
      </label>
      <label>
        <span>读数值</span>
        <input v-model="reportForm.value" inputmode="decimal" data-testid="report-value" aria-label="读数值" />
      </label>
      <label>
        <span>来源渠道</span>
        <input v-model="reportForm.source" data-testid="report-source" aria-label="来源渠道" />
      </label>
      <label class="source-label-field">
        <span>来源显示名</span>
        <input v-model="reportForm.source_label" data-testid="report-source-label" aria-label="来源显示名" />
      </label>
      <button class="btn primary" type="submit" data-testid="submit-reading">上报读数</button>
      <button
        class="btn"
        type="button"
        data-testid="send-10-twice"
        @click="sendSameValueTwice"
      >
        10 连续送两次
      </button>
      <button
        class="btn"
        type="button"
        data-testid="resend-same-10"
        @click="quickReport(10, 'channel-A', '采集渠道 A')"
      >
        同源再送一次 10
      </button>
      <button
        class="btn"
        type="button"
        data-testid="report-other-source-14"
        @click="quickReport(14, 'channel-B', '另一渠道 B')"
      >
        另一渠道给 14
      </button>
      <button class="btn ghost" type="button" data-testid="read-target-window" @click="readTargetWindow">
        让窗口读取当前值
      </button>
    </form>

    <p v-if="feedback" class="recon-feedback" data-testid="recon-feedback" role="status">{{ feedback }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="recon-error" role="alert">{{ errorMessage }}</p>

    <div class="recon-summary" data-testid="recon-summary">
      <span>P·11:00 不同读数：<strong>{{ state?.summary?.p11_unique_readings ?? 0 }}</strong> 条</span>
      <span>当前有效样本：<strong>{{ state?.summary?.p11_effective_samples ?? 0 }}</strong> 项</span>
      <span>待裁定争议单：<strong>{{ state?.summary?.open_disputes ?? 0 }}</strong> 张</span>
      <span>未确认窗口：<strong>{{ state?.summary?.unconfirmed_windows ?? 0 }}</strong> 个</span>
    </div>

    <div class="recon-grid">
      <article class="recon-card" data-testid="sample-list-card">
        <div class="card-head">
          <h4>同位样本与来源核对</h4>
          <span>同值重送不新增样本</span>
        </div>
        <table class="recon-table" data-testid="sample-table">
          <thead>
            <tr>
              <th>采样身份</th>
              <th>时间</th>
              <th>数值</th>
              <th>来源</th>
              <th>投递次数</th>
              <th>状态/资格</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="sample in state?.samples ?? []"
              :key="sample.id"
              :data-testid="`sample-row-${sample.id}`"
              :data-valid="String(Boolean(sample.is_valid))"
            >
              <td>{{ sample.identity }}</td>
              <td>{{ sample.sample_time }}</td>
              <td><strong>{{ sample.value }}</strong></td>
              <td>
                <div v-for="source in sample.sources ?? []" :key="source.source" class="source-line">
                  {{ source.source_label || source.source }}
                  <code>{{ source.source }}</code>
                  <em v-if="source.delivery_count > 1">×{{ source.delivery_count }}</em>
                </div>
              </td>
              <td data-testid="delivery-count">{{ sample.delivery_count }}</td>
              <td>
                <span class="badge" :data-valid="String(Boolean(sample.is_valid))">
                  {{ sample.is_valid ? '有效样本' : sample.status }}
                </span>
              </td>
            </tr>
            <tr v-if="!(state?.samples ?? []).length">
              <td colspan="6" class="empty-state">暂无上报读数，可使用上方按钮构造 P·11:00 场景。</td>
            </tr>
          </tbody>
        </table>
      </article>

      <article class="recon-card" data-testid="dispute-list-card">
        <div class="card-head">
          <h4>争议处理单</h4>
          <span>两个来源 · 登记采用与拒绝依据</span>
        </div>
        <div
          v-for="dispute in state?.disputes ?? []"
          :key="dispute.id"
          class="dispute-item"
          :data-testid="`dispute-card-${dispute.id}`"
          :data-status="dispute.status"
        >
          <div class="dispute-title">
            <strong data-testid="dispute-no">{{ dispute.dispute_no || dispute.id }}</strong>
            <span class="badge" :data-resolved="String(dispute.status === '已裁定')">{{ dispute.status }}</span>
          </div>
          <p class="dispute-position">测点 {{ dispute.point_code }} · {{ dispute.sample_time }}</p>
          <div class="source-options-readonly">
            <span
              v-for="candidate in dispute.candidates ?? []"
              :key="candidate.sample_id"
              :data-testid="`dispute-source-${candidate.sample_id}`"
              :data-adopted="String(dispute.adopted_sample_id === candidate.sample_id)"
              :data-rejected="String(dispute.rejected_sample_id === candidate.sample_id)"
            >
              {{ candidate.source_label }}：<strong>{{ candidate.value }}</strong>
              <small>（投递 {{ candidate.delivery_count }} 次）</small>
              <b v-if="dispute.adopted_sample_id === candidate.sample_id" class="adopted-tag">已采用</b>
              <b v-if="dispute.rejected_sample_id === candidate.sample_id" class="rejected-tag">已拒绝</b>
            </span>
          </div>
          <div v-if="dispute.status === '已裁定'" class="dispute-basis-inline" :data-testid="`basis-inline-${dispute.id}`">
            <p data-testid="inline-adopted-basis"><b>采用依据：</b>{{ dispute.adopted_basis }}</p>
            <p data-testid="inline-rejected-basis"><b>拒绝依据：</b>{{ dispute.rejected_basis }}</p>
          </div>
          <button
            class="link"
            type="button"
            :data-testid="`open-dispute-${dispute.id}`"
            @click="openDispute(String(dispute.id))"
          >
            {{ dispute.status === '已裁定' ? '重新打开第一次处置和后续结果' : '查看冲突并裁定' }}
          </button>
        </div>
        <p v-if="!(state?.disputes ?? []).length" class="empty-state">尚未出现同位异值争议。</p>
      </article>
    </div>

    <article class="recon-card window-card" data-testid="window-list-card">
      <div class="card-head">
        <h4>依赖窗口、继续处理与最终核对</h4>
        <span>无关窗口不参与重算</span>
      </div>
      <table class="recon-table window-table" data-testid="window-table">
        <thead>
          <tr>
            <th>窗口</th>
            <th>预读值</th>
            <th>状态</th>
            <th>正式出值/使用样本</th>
            <th>资格与核对</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="window in state?.windows ?? []"
            :key="window.id"
            :data-testid="`window-row-${window.id}`"
            :data-recomputed="String(Boolean(window.recomputed))"
            :data-status="window.status"
          >
            <td>
              <strong>{{ window.title || window.id }}</strong>
              <small>{{ window.range_label }}</small>
            </td>
            <td data-testid="window-read-value">
              {{ window.read_value ?? '—' }}
              <small v-if="window.read_sample_id">{{ window.read_sample_id }}</small>
            </td>
            <td data-testid="window-status">{{ window.status }}</td>
            <td data-testid="window-formal-output">
              <template v-if="window.formal_output">
                <strong>{{ window.formal_output.value }}</strong>
                <small>{{ window.formal_output.sample_id }} · {{ window.formal_output.source_label || window.formal_output.source }}</small>
              </template>
              <template v-else-if="window.used_value !== null && window.used_value !== undefined">
                待签收：{{ window.used_value }}
                <small>{{ window.used_sample_id }}</small>
              </template>
              <span v-else>尚未使用裁决样本</span>
            </td>
            <td data-testid="window-verification">
              <span v-if="window.verification?.conclusion">{{ window.verification.conclusion }}</span>
              <span v-else>有效样本资格数：{{ window.eligible_sample_count || 0 }}</span>
              <small v-if="window.id === 'WIN-P-1200-SIDE'" data-testid="side-window-unchanged">
                旁窗无变化，重算次数 {{ window.recompute_count || 0 }}
              </small>
            </td>
            <td class="window-actions">
              <button
                class="link"
                type="button"
                :data-testid="`continue-window-${window.id}`"
                @click="continueWindow(String(window.id))"
              >
                继续处理
              </button>
              <button
                class="link"
                type="button"
                :data-testid="`detail-window-${window.id}`"
                @click="openWindow(String(window.id))"
              >
                核对详情
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>
    </div>

    <!-- 争议处理单：查看两个来源、选择采用值、回看第一次处置和后续结果 -->
    <div
      v-if="selectedDispute"
      class="modal-backdrop recon-modal-backdrop"
      data-testid="dispute-modal"
      @click.self="closeDispute"
    >
      <form
        class="modal dispute-modal"
        data-testid="dispute-detail"
        role="dialog"
        aria-modal="true"
        aria-label="争议处理单详情"
        @submit.prevent="submitRuling(false)"
      >
        <div class="modal-title-row">
          <h3>争议处理单 {{ selectedDispute.dispute_no || selectedDispute.id }}</h3>
          <button class="link" type="button" data-testid="close-dispute" @click="closeDispute">关闭</button>
        </div>
        <p class="dispute-position">
          采样身份 <code>{{ selectedDispute.identity }}</code> · 测点 {{ selectedDispute.point_code }}
          · {{ selectedDispute.sample_time }} · 状态 {{ selectedDispute.status }}
        </p>

        <fieldset :disabled="selectedDispute.status === '已裁定'" class="candidate-fieldset">
          <legend>两个来源的同位读数（请选择最终采用值）</legend>
          <label
            v-for="candidate in selectedDispute.candidates ?? []"
            :key="candidate.sample_id"
            class="candidate-option"
            :data-testid="`candidate-option-${candidate.sample_id}`"
            :data-selected="String(ruling.sample_id === candidate.sample_id)"
            :data-value="String(candidate.value)"
            :data-source="String(candidate.source)"
          >
            <input
              v-model="ruling.sample_id"
              type="radio"
              name="adopted-sample"
              :value="candidate.sample_id"
              :data-testid="`adopt-radio-${candidate.value}`"
              :aria-label="`采用 ${candidate.source_label} 来源的 ${candidate.value}`"
            />
            <span class="candidate-main">
              <strong>{{ candidate.value }}</strong>
              <span>{{ candidate.source_label }}（{{ candidate.source }}）</span>
              <small>相同值重送/投递 {{ candidate.delivery_count }} 次，去重计数 {{ candidate.duplicate_count }}。</small>
              <small>{{ candidate.evidence }}</small>
              <button
                class="btn ghost choose-candidate"
                type="button"
                :data-testid="`choose-value-${candidate.value}`"
                @click.prevent.stop="ruling.sample_id = candidate.sample_id"
              >
                采用 {{ candidate.value }}
              </button>
            </span>
          </label>
        </fieldset>

        <label class="ruling-reason-field">
          <span>裁决/核对说明（可选，将同时进入采用与拒绝依据）</span>
          <textarea
            v-model="ruling.reason"
            rows="3"
            data-testid="ruling-reason"
            placeholder="例如：现场双通道核对、仪表复核或上游质检记录支持 14。"
          />
        </label>

        <div v-if="selectedDispute.status !== '已裁定'" class="modal-actions">
          <button class="btn" type="button" @click="closeDispute">取消</button>
          <button class="btn" type="submit" data-testid="submit-ruling" :disabled="busy || !ruling.sample_id">
            {{ busy ? '提交中…' : '确认裁决并登记' }}
          </button>
          <button
            class="btn primary"
            type="button"
            data-testid="submit-ruling-and-continue"
            :disabled="busy || !ruling.sample_id"
            @click="submitRuling(true)"
          >
            选 14 后继续处理
          </button>
        </div>

        <div v-else class="ruling-record" data-testid="ruling-record">
          <h4>第一次处置（可重新打开，不覆盖）</h4>
          <p><b>采用：</b>{{ selectedDispute.adopted_source_label || selectedDispute.adopted_source }} 的
            <strong data-testid="adopted-value">{{ selectedDispute.adopted_value }}</strong>
          </p>
          <p data-testid="adopted-basis"><b>采用依据：</b>{{ selectedDispute.adopted_basis }}</p>
          <p><b>拒绝：</b>{{ selectedDispute.rejected_source }} 的
            <strong data-testid="rejected-value">{{ selectedDispute.rejected_value }}</strong>
          </p>
          <p data-testid="rejected-basis"><b>拒绝依据：</b>{{ selectedDispute.rejected_basis }}</p>
          <button
            class="btn primary"
            type="button"
            data-testid="continue-from-dispute"
            @click="continueTargetFromDispute"
          >
            继续处理 WIN-P-1100
          </button>
        </div>

        <div v-if="selectedDispute.linked_windows?.length" class="linked-results" data-testid="linked-results">
          <h4>后续结果：依赖该位置的窗口</h4>
          <article v-for="window in selectedDispute.linked_windows" :key="window.id" class="linked-window">
            <header>
              <strong>{{ window.id }}</strong>
              <span>{{ window.status }}</span>
            </header>
            <p v-if="window.formal_output">
              正式出值 <b>{{ window.formal_output.value }}</b>；使用样本
              <code>{{ window.formal_output.sample_id }}</code>，来源
              {{ window.formal_output.source_label || window.formal_output.source }}。
              {{ window.formal_output.eligibility }}
            </p>
            <p v-else-if="window.verification?.conclusion">{{ window.verification.conclusion }}</p>
            <p v-if="window.verification?.conclusion" class="check-conclusion">
              {{ window.verification.conclusion }}
            </p>
            <ul class="timeline-list" data-testid="dispute-window-timeline">
              <li v-for="(event, index) in window.timeline ?? []" :key="index">
                <time>{{ event.at }}</time> {{ event.message }}
              </li>
            </ul>
          </article>
        </div>
      </form>
    </div>

    <!-- 窗口后续结果回看 -->
    <div
      v-if="windowDetail"
      class="modal-backdrop recon-modal-backdrop"
      data-testid="window-modal"
      @click.self="windowDetail = null"
    >
      <div
        class="modal window-detail-modal"
        data-testid="window-detail"
        role="dialog"
        aria-modal="true"
        aria-label="窗口核对详情"
      >
        <div class="modal-title-row">
          <h3>窗口核对详情 · {{ windowDetail.id }}</h3>
          <button class="link" type="button" data-testid="close-window-detail" @click="windowDetail = null">关闭</button>
        </div>
        <p><b>状态：</b><span data-testid="detail-status">{{ windowDetail.status }}</span></p>
        <p><b>预读值：</b>{{ windowDetail.read_value }}（{{ windowDetail.read_sample_id || '无样本编号' }}）</p>
        <p><b>是否重算：</b><span data-testid="detail-recomputed">{{ windowDetail.recomputed ? '是' : '否' }}</span></p>
        <p v-if="windowDetail.formal_output" data-testid="detail-formal-output">
          <b>正式出值：</b>{{ windowDetail.formal_output.value }}<br />
          <b>实际使用样本：</b><code>{{ windowDetail.formal_output.sample_id }}</code><br />
          <b>来源：</b>{{ windowDetail.formal_output.source_label || windowDetail.formal_output.source }}<br />
          <b>资格说明：</b>{{ windowDetail.formal_output.eligibility }}
        </p>
        <p v-if="windowDetail.verification?.conclusion" class="check-conclusion" data-testid="detail-conclusion">
          {{ windowDetail.verification.conclusion }}
        </p>
        <ul class="timeline-list" data-testid="window-timeline">
          <li v-for="(event, index) in windowDetail.timeline ?? []" :key="index">
            <time>{{ event.at }}</time> <b>{{ event.actor }}</b>：{{ event.message }}
          </li>
        </ul>
        <div class="modal-actions">
          <button class="btn" type="button" @click="windowDetail = null">关闭</button>
          <button
            class="btn primary"
            type="button"
            data-testid="detail-continue"
            @click="continueWindow(String(windowDetail.id))"
          >
            继续处理/签收
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  adjudicateDispute,
  continueReconciliationWindow,
  getDisputeDetail,
  getReconciliationState,
  readReconciliationWindow,
  reopenDispute,
  reportSample,
  resetReconciliation,
  type ReconciliationState,
} from '@/api/backend-service'

const state = ref<ReconciliationState | null>(null)
const selectedDispute = ref<Record<string, any> | null>(null)
const windowDetail = ref<Record<string, any> | null>(null)
const feedback = ref('')
const errorMessage = ref('')
const busy = ref(false)

const reportForm = reactive({
  point_code: 'P',
  sample_time: '11:00',
  value: '10',
  source: 'channel-A',
  source_label: '采集渠道 A',
})
const ruling = reactive({ sample_id: '', reason: '' })

async function load() {
  errorMessage.value = ''
  try {
    state.value = await getReconciliationState()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '冲突数据读取失败'
  }
}

async function resetDemo(scenario: string) {
  busy.value = true
  errorMessage.value = ''
  feedback.value = ''
  try {
    state.value = await resetReconciliation(scenario)
    selectedDispute.value = null
    windowDetail.value = null
    feedback.value = scenario === 'conflict'
      ? '已载入：P·11:00 的 10 重送两次仅一条，另一渠道 14 已产生争议，窗口预读 10 未签收。'
      : '已准备：10 由同一来源送两次，窗口已读取 10 但尚未签收。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景重置失败'
  } finally {
    busy.value = false
  }
}

async function submitReport() {
  busy.value = true
  errorMessage.value = ''
  feedback.value = ''
  try {
    const payload = { ...reportForm, value: Number(reportForm.value) }
    const result = await reportSample(payload)
    if (result.ok === false) throw new Error(result.message || '上报失败')
    feedback.value = result.message || '读数已上报'
    await load()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '上报失败'
  } finally {
    busy.value = false
  }
}

async function quickReport(value: number, source: string, sourceLabel: string) {
  reportForm.value = String(value)
  reportForm.source = source
  reportForm.source_label = sourceLabel
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await reportSample({
      point_code: reportForm.point_code,
      sample_time: reportForm.sample_time,
      value,
      source,
      source_label: sourceLabel,
    })
    if (result.ok === false) throw new Error(result.message || '上报失败')
    feedback.value = result.message || '读数已上报'
    await load()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '上报失败'
  } finally {
    busy.value = false
  }
}

async function readTargetWindow() {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await readReconciliationWindow({ window_id: 'WIN-P-1100' })
    if (result.ok === false) throw new Error(result.message || '窗口读取失败')
    feedback.value = result.message || '窗口已读取，尚未签收'
    await load()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '窗口读取失败'
  } finally {
    busy.value = false
  }
}

async function sendSameValueTwice() {
  busy.value = true
  errorMessage.value = ''
  feedback.value = ''
  try {
    const payload = {
      point_code: reportForm.point_code,
      sample_time: reportForm.sample_time,
      value: 10,
      source: 'channel-A',
      source_label: '采集渠道 A',
    }
    await reportSample(payload)
    const second = await reportSample(payload)
    if (second.ok === false) throw new Error(second.message || '第二次上报失败')
    feedback.value = '10 已送两次；相同采样身份和相同值幂等去重，仍只有一条样本。'
    await load()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '连续上报失败'
  } finally {
    busy.value = false
  }
}

async function openDispute(disputeId: string, trackReopen = true) {
  errorMessage.value = ''
  try {
    const result = trackReopen ? await reopenDispute(disputeId) : await getDisputeDetail(disputeId)
    if (result.ok === false) throw new Error(result.message || '争议单读取失败')
    const detail: Record<string, any> = result.dispute ?? result.entry ?? result
    selectedDispute.value = detail
    ruling.sample_id = String(detail.adopted_sample_id ?? '')
    ruling.reason = String(detail.ruling_reason ?? '')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '争议单读取失败'
  }
}

function closeDispute() {
  selectedDispute.value = null
}

async function submitRuling(andContinue = false) {
  if (!ruling.sample_id) {
    errorMessage.value = '请先选择要采用的来源读数'
    return
  }
  if (!selectedDispute.value) return
  const disputeId = String(selectedDispute.value.id)
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await adjudicateDispute(disputeId, {
      sample_id: ruling.sample_id,
      reason: ruling.reason,
      operator: '值班操作员',
    })
    if (result.ok === false) throw new Error(result.message || '裁决失败')
    feedback.value = result.message || '裁决已登记，依赖窗口已按确认值处理'
    await load()
    if (andContinue) {
      await openDispute(disputeId, false)
      await continueWindow('WIN-P-1100')
    } else {
      // 回到工作台列表，让操作人继续点窗口行的“继续处理”；依据已内联在争议单中，也可重新打开。
      selectedDispute.value = null
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '裁决失败'
  } finally {
    busy.value = false
  }
}

async function continueWindow(windowId: string) {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await continueReconciliationWindow(windowId, { operator: '值班操作员' })
    if (result.ok === false) throw new Error(result.message || '继续处理失败')
    feedback.value = result.message || '窗口已继续处理'
    // 不主动弹出第二层弹窗；若详情弹窗本来开着，则原地刷新它。
    if (windowDetail.value) windowDetail.value = result.window ?? null
    await load()
    if (selectedDispute.value?.id) await openDispute(String(selectedDispute.value.id), false)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '继续处理失败'
  } finally {
    busy.value = false
  }
}

async function continueTargetFromDispute() {
  await continueWindow('WIN-P-1100')
}

async function openWindow(windowId: string) {
  const row = state.value?.windows.find((item) => String(item.id) === windowId)
  windowDetail.value = row ?? null
}

onMounted(load)
</script>

<style scoped>
.recon-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin: 14px 0 18px;
}
.recon-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.recon-head h3 { margin: 0 0 6px; font-size: 18px; }
.recon-head p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.6; }
.recon-demo-actions { display: flex; gap: 8px; flex-shrink: 0; }
.recon-ingest {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
  padding: 12px;
  margin: 14px 0 8px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.recon-ingest label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.recon-ingest input { border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; min-width: 130px; color: #1f2937; }
.recon-ingest .source-label-field input { min-width: 150px; }
.recon-feedback { color: #067647; font-size: 13px; margin: 8px 0; }
.recon-summary { display: flex; flex-wrap: wrap; gap: 16px; padding: 10px 12px; background: #eff6ff; border-radius: 8px; font-size: 13px; margin: 10px 0 14px; }
.recon-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); gap: 14px; }
.recon-card { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: #fff; min-width: 0; }
.window-card { margin-top: 14px; }
.card-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; margin-bottom: 10px; }
.card-head h4 { margin: 0; font-size: 15px; }
.card-head span { color: var(--muted); font-size: 12px; }
.recon-table { width: 100%; border-collapse: collapse; }
.recon-table th, .recon-table td { border-bottom: 1px solid #eef2f7; padding: 8px; font-size: 12px; text-align: left; vertical-align: top; }
.recon-table th { color: var(--muted); font-weight: 600; background: #fafbfc; }
.source-line { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin-bottom: 3px; }
.source-line code, .dispute-position code, p code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 11px; }
.source-line em { color: #b45309; font-style: normal; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #fef3c7; color: #92400e; font-size: 12px; white-space: nowrap; }
.badge[data-valid="true"], .badge[data-resolved="true"] { background: #dcfce7; color: #166534; }
.dispute-item { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
.dispute-item[data-status="待裁定"] { border-color: #f59e0b; background: #fffbeb; }
.dispute-title { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.dispute-position { color: var(--muted); font-size: 12px; margin: 6px 0; }
.source-options-readonly { display: grid; gap: 5px; font-size: 13px; margin: 8px 0; }
.adopted-tag { color: #166534; margin-left: 6px; }
.rejected-tag { color: #b42318; margin-left: 6px; }
.dispute-basis-inline { display: grid; gap: 4px; margin: 8px 0; padding: 8px; background: #f8fafc; border-radius: 6px; }
.dispute-basis-inline p { margin: 0; font-size: 12px; line-height: 1.55; color: #475569; }
.window-table small { display: block; color: var(--muted); margin-top: 3px; }
.window-actions { white-space: nowrap; }
.window-actions .link { margin-right: 10px; }
.recon-modal-backdrop { z-index: 30; }
.dispute-modal { width: min(820px, 100%); }
.modal-title-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.modal-title-row h3 { margin: 0; }
.candidate-fieldset { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin: 12px 0; }
.candidate-fieldset legend { padding: 0 6px; font-size: 13px; color: var(--muted); }
.candidate-option { display: flex; gap: 10px; align-items: flex-start; padding: 10px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; cursor: pointer; }
.candidate-option[data-selected="true"] { border-color: var(--brand); background: #eff6ff; }
.candidate-option input { margin-top: 3px; }
.candidate-main { display: grid; gap: 3px; font-size: 13px; }
.candidate-main strong { font-size: 18px; }
.candidate-main small { color: var(--muted); line-height: 1.5; }
.choose-candidate { align-self: flex-start; padding: 3px 10px; font-size: 12px; margin-top: 2px; }
.ruling-reason-field { display: grid; gap: 5px; font-size: 13px; color: var(--muted); }
.ruling-reason-field textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px; font: inherit; color: #1f2937; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.ruling-record, .linked-results { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 12px; font-size: 13px; line-height: 1.65; }
.ruling-record h4, .linked-results h4 { margin: 0 0 8px; }
.ruling-record p { margin: 6px 0; }
.linked-window { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
.linked-window header { display: flex; justify-content: space-between; font-size: 13px; }
.check-conclusion { color: #166534; font-weight: 600; }
.timeline-list { margin: 8px 0 0; padding-left: 18px; display: grid; gap: 5px; font-size: 12px; color: #475569; }
.timeline-list time { color: var(--muted); margin-right: 6px; }
.window-detail-modal { width: min(720px, 100%); }
@media (max-width: 980px) {
  .recon-head, .recon-grid { grid-template-columns: 1fr; display: grid; }
  .recon-demo-actions { flex-wrap: wrap; }
}
</style>
