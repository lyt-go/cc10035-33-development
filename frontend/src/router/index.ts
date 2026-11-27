import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import DisputeCenterView from '@/views/DisputeCenterView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/disputes', name: 'disputes', component: DisputeCenterView },
    { path: '/ts_dispute', redirect: '/disputes' },
    { path: '/ts_disputes', redirect: '/disputes' },
    { path: '/ts-point/disputes', redirect: '/disputes' },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
