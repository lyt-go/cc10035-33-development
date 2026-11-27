<template>
  <section class="dispute-center" data-module="ts_dispute">
    <header class="page-head">
      <div>
        <h2>争议处理单</h2>
        <p class="page-desc">
          辨认同一采样身份的多来源异值，登记采用与拒绝依据；裁决结果只驱动依赖该位置的未确认窗口，
          并保留可重新打开的第一次处置和后续出值结果。
        </p>
      </div>
    </header>
    <TsReconciliationPanel />
  </section>
</template>

<script setup lang="ts">
import TsReconciliationPanel from '@/components/TsReconciliationPanel.vue'
</script>
