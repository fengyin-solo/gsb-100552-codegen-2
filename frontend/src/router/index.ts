import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from '@/views/Dashboard.vue'
const PvArray = () => import('@/views/pv_array/index.vue')
const Inverter = () => import('@/views/inverter/index.vue')
const CombinerBox = () => import('@/views/combiner_box/index.vue')
const Transformer = () => import('@/views/transformer/index.vue')
const EnergyStorage = () => import('@/views/energy_storage/index.vue')
const BoostingStation = () => import('@/views/boosting_station/index.vue')
const Meter = () => import('@/views/meter/index.vue')
const Environment = () => import('@/views/environment/index.vue')
const Cleaning = () => import('@/views/cleaning/index.vue')
const Patrol = () => import('@/views/patrol/index.vue')
const Defect = () => import('@/views/defect/index.vue')
const Maintenance = () => import('@/views/maintenance/index.vue')
const SpareParts = () => import('@/views/spare_parts/index.vue')
const Alarm = () => import('@/views/alarm/index.vue')
const Dispatch = () => import('@/views/dispatch/index.vue')
const Safety = () => import('@/views/safety/index.vue')
const Contract = () => import('@/views/contract/index.vue')
const Report = () => import('@/views/report/index.vue')
const CarbonLedger = () => import('@/views/carbon_ledger/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/pv_array', name: 'pv_array', component: PvArray },
    { path: '/inverter', name: 'inverter', component: Inverter },
    { path: '/combiner_box', name: 'combiner_box', component: CombinerBox },
    { path: '/transformer', name: 'transformer', component: Transformer },
    { path: '/energy_storage', name: 'energy_storage', component: EnergyStorage },
    { path: '/boosting_station', name: 'boosting_station', component: BoostingStation },
    { path: '/meter', name: 'meter', component: Meter },
    { path: '/environment', name: 'environment', component: Environment },
    { path: '/cleaning', name: 'cleaning', component: Cleaning },
    { path: '/patrol', name: 'patrol', component: Patrol },
    { path: '/defect', name: 'defect', component: Defect },
    { path: '/maintenance', name: 'maintenance', component: Maintenance },
    { path: '/spare_parts', name: 'spare_parts', component: SpareParts },
    { path: '/alarm', name: 'alarm', component: Alarm },
    { path: '/dispatch', name: 'dispatch', component: Dispatch },
    { path: '/safety', name: 'safety', component: Safety },
    { path: '/contract', name: 'contract', component: Contract },
    { path: '/report', name: 'report', component: Report },
    { path: '/carbon_ledger', name: 'carbon_ledger', component: CarbonLedger },
  ],
})

export default router
