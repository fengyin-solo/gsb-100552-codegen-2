<template>
  <section class="page" data-module="green_asset">
    <header class="page-head">
      <div>
        <h2>绿证碳资产台账</h2>
        <p class="page-desc">
          按已发布运行月报发电量核发绿证与碳减排量，逐月登记交易结算与结余。
          列表、全量导出、分页导出、重新进入同一份台账口径；冲突时以台账为准。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="runReconcile">立即对账</button>
        <button class="btn" type="button" @click="exportJson">导出对账文件(JSON)</button>
        <button class="btn primary" type="button" @click="exportCsv">导出对账文件(CSV)</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p v-if="message" class="page-desc" :class="{ 'error-text': !messageOk }">{{ message }}</p>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>台账月份</span>
        <input v-model="filters.month" placeholder="YYYY-MM 精确筛选" />
      </label>
      <label class="filter-item">
        <span>口径状态</span>
        <input v-model="filters.status" placeholder="已关账 / 未关账" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      <span class="page-desc">口径校验 checksum：{{ checksum ? checksum.slice(0, 16) + '…' : '—' }}</span>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length" class="empty-state">暂无台账行</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 个台账月份 · 第 {{ page }} 页</span>
      <button class="link" type="button" :disabled="page <= 1" @click="changePage(-1)">上一页</button>
      <button class="link" type="button" :disabled="rows.length < size" @click="changePage(1)">下一页</button>
    </footer>

    <div class="stat-row" style="margin-top:24px">
      <article class="stat-card" style="flex:1">
        <span class="stat-label">月末关账（核发+结算一次事务）</span>
        <form class="filter-bar" @submit.prevent="closeMonth">
          <label class="filter-item"><span>关账月份</span><input v-model="closeForm.月份" placeholder="YYYY-MM" /></label>
          <label class="filter-item"><span>凭证号</span><input v-model="closeForm.凭证号" placeholder="可留空，多张请先逐笔登记" /></label>
          <label class="filter-item">
            <span>资产类型</span>
            <select v-model="closeForm.资产类型"><option>绿证</option><option>碳减排量</option></select>
          </label>
          <label class="filter-item"><span>数量</span><input v-model="closeForm.数量" placeholder="正数" /></label>
          <button class="btn primary" type="submit">一次事务关账</button>
        </form>
      </article>
    </div>

    <div class="stat-row">
      <article class="stat-card" style="flex:1">
        <span class="stat-label">登记交易结算（凭证号重复只保留最初一条）</span>
        <form class="filter-bar" @submit.prevent="registerSettlement">
          <label class="filter-item"><span>凭证号</span><input v-model="settleForm.凭证号" /></label>
          <label class="filter-item">
            <span>资产类型</span>
            <select v-model="settleForm.资产类型"><option>绿证</option><option>碳减排量</option></select>
          </label>
          <label class="filter-item"><span>数量</span><input v-model="settleForm.数量" /></label>
          <label class="filter-item"><span>结算月份</span><input v-model="settleForm.结算月份" placeholder="YYYY-MM" /></label>
          <label class="filter-item"><span>交易对手</span><input v-model="settleForm.交易对手" /></label>
          <button class="btn primary" type="submit">登记凭证</button>
        </form>
      </article>
      <article class="stat-card" style="flex:1">
        <span class="stat-label">核发规则新版本（已核发月份不回算）</span>
        <form class="filter-bar" @submit.prevent="addRule">
          <label class="filter-item"><span>版本</span><input v-model="ruleForm.规则版本" placeholder="如 v2" /></label>
          <label class="filter-item"><span>生效月份</span><input v-model="ruleForm.生效月份" placeholder="YYYY-MM" /></label>
          <label class="filter-item"><span>每证兆瓦时</span><input v-model="ruleForm.每证兆瓦时" /></label>
          <label class="filter-item"><span>碳减排因子</span><input v-model="ruleForm.碳减排因子" /></label>
          <button class="btn" type="submit">登记规则版本</button>
        </form>
      </article>
    </div>

    <h3 style="margin-top:24px">结算凭证明细</h3>
    <table class="data-table">
      <thead>
        <tr><th v-for="c in settleColumns" :key="c">{{ c }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in settlements" :key="row.id">
          <td v-for="c in settleColumns" :key="c">{{ row[c] ?? '—' }}</td>
        </tr>
        <tr v-if="!settlements.length">
          <td :colspan="settleColumns.length" class="empty-state">暂无结算凭证</td>
        </tr>
      </tbody>
    </table>

    <h3 style="margin-top:24px">逐月核发记录（冻结核发当时规则）</h3>
    <table class="data-table">
      <thead>
        <tr><th v-for="c in issuanceColumns" :key="c">{{ c }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in issuances" :key="row.id">
          <td v-for="c in issuanceColumns" :key="c">{{ row[c] ?? '—' }}</td>
        </tr>
        <tr v-if="!issuances.length">
          <td :colspan="issuanceColumns.length" class="empty-state">暂无核发记录</td>
        </tr>
      </tbody>
    </table>

    <h3 style="margin-top:24px">核发规则版本</h3>
    <table class="data-table">
      <thead>
        <tr><th>规则版本</th><th>生效月份</th><th>每证兆瓦时</th><th>碳减排因子</th><th>算法</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rules" :key="row.id">
          <td>{{ row['规则版本'] }}</td><td>{{ row['生效月份'] }}</td>
          <td>{{ row['每证兆瓦时'] }}</td><td>{{ row['碳减排因子'] }}</td><td>{{ row['算法'] }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/green_asset'

const columns = ['台账月份', '口径状态', '规则版本', '期初绿证', '核发绿证', '结算绿证', '结余绿证',
  '期初碳减排量', '核发碳减排量', '结算碳减排量', '结余碳减排量', '关账时间']
const settleColumns = ['凭证号', '资产类型', '数量', '结算月份', '交易对手', '金额', '登记时间']
const issuanceColumns = ['核发月份', '月报编号', '发电量MWh', '核发绿证', '核发碳减排量', '规则版本', '碳减排因子']

const rows = ref<Record<string, string | number>[]>([])
const total = ref(0)
const page = ref(1)
const size = 20
const checksum = ref('')
const totals = ref<Record<string, number>>({})
const message = ref('')
const messageOk = ref(true)
const filters = reactive<Record<string, string>>({ month: '', status: '' })

const settlements = ref<Record<string, string | number>[]>([])
const issuances = ref<Record<string, string | number>[]>([])
const rules = ref<Record<string, string | number>[]>([])

const settleForm = reactive({ 凭证号: '', 资产类型: '绿证', 数量: '', 结算月份: '', 交易对手: '' })
const closeForm = reactive({ 月份: '', 凭证号: '', 资产类型: '绿证', 数量: '' })
const ruleForm = reactive({ 规则版本: '', 生效月份: '', 每证兆瓦时: '1', 碳减排因子: '' })

const statCards = computed(() => [
  { label: '台账月份数', value: total.value },
  { label: '总结余绿证(个)', value: totals.value['总结余绿证'] ?? 0 },
  { label: '总结余碳减排量(吨)', value: totals.value['总结余碳减排量'] ?? 0 },
  { label: '累计结算绿证', value: totals.value['累计结算绿证'] ?? 0 },
])

function notify(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

async function call(path: string, init?: RequestInit) {
  const response = await request(`${ENDPOINT}${path}`, init)
  const payload = await response.json()
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '操作未生效')
  return payload
}

function queryString(p = page.value) {
  const params = new URLSearchParams({ page: String(p), size: String(size) })
  if (filters.month) params.set('month', filters.month)
  if (filters.status) params.set('status', filters.status)
  return params.toString()
}

async function reload() {
  try {
    const payload = await call(`/ledger?${queryString()}`)
    rows.value = payload.items ?? []
    total.value = payload.total ?? 0
    checksum.value = payload.checksum ?? ''
    totals.value = payload.totals ?? {}
    await Promise.all([loadAux()])
  } catch (error) {
    notify(error instanceof Error ? error.message : '台账读取失败', false)
  }
}

async function loadAux() {
  const [s, i, r] = await Promise.all([call('/settlements'), call('/issuances'), call('/rules')])
  settlements.value = s.items ?? []
  issuances.value = i.items ?? []
  rules.value = r.items ?? []
}

function resetFilters() {
  filters.month = ''
  filters.status = ''
  page.value = 1
  void reload()
}

function changePage(delta: number) {
  page.value = Math.max(1, page.value + delta)
  void reload()
}

async function registerSettlement() {
  try {
    const payload = await call('/settlements', {
      method: 'POST',
      body: JSON.stringify({
        凭证号: settleForm.凭证号, 资产类型: settleForm.资产类型,
        数量: Number(settleForm.数量), 结算月份: settleForm.结算月份,
        交易对手: settleForm.交易对手,
      }),
    })
    notify(payload.message)
    Object.assign(settleForm, { 凭证号: '', 数量: '', 结算月份: '', 交易对手: '' })
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '结算登记失败', false)
  }
}

async function closeMonth() {
  const 结算凭证 = closeForm.凭证号
    ? [{ 凭证号: closeForm.凭证号, 资产类型: closeForm.资产类型, 数量: Number(closeForm.数量), 结算月份: closeForm.月份 }]
    : []
  try {
    const payload = await call('/close_month', {
      method: 'POST', body: JSON.stringify({ 月份: closeForm.月份, 结算凭证 }),
    })
    notify(payload.message)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '月末关账失败（整批未写入）', false)
  }
}

async function addRule() {
  try {
    const payload = await call('/rules', {
      method: 'POST',
      body: JSON.stringify({
        规则版本: ruleForm.规则版本, 生效月份: ruleForm.生效月份,
        每证兆瓦时: Number(ruleForm.每证兆瓦时), 碳减排因子: Number(ruleForm.碳减排因子),
      }),
    })
    notify(payload.message)
    await loadAux()
  } catch (error) {
    notify(error instanceof Error ? error.message : '规则登记失败', false)
  }
}

async function runReconcile() {
  try {
    const payload = await call('/reconcile')
    notify(payload.ok ? '对账一致：核发侧、凭证侧与台账结余三方平账' : `对账不平：${payload.mismatches.join('；')}`, payload.ok)
  } catch (error) {
    notify(error instanceof Error ? error.message : '对账失败', false)
  }
}

function exportJson() {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.status) params.set('status', filters.status)
  window.open(`${ENDPOINT}/ledger/export?${params.toString()}`, '_blank')
}

function exportCsv() {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.status) params.set('status', filters.status)
  window.open(`${ENDPOINT}/ledger/export.csv?${params.toString()}`, '_blank')
}

onMounted(reload)
</script>
