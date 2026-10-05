<template>
  <section class="page" data-module="carbon-ledger">
    <header class="page-head">
      <div>
        <h2>绿证碳资产台账</h2>
        <p class="page-desc">
          按运行月报发电量口径核发绿证与碳减排量，逐月登记交易结算与结余；
          结余由流水逐笔推导，列表与对账文件同一份口径，冲突时以台账为准。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openPanel('close')">月末核发与结算</button>
        <button class="btn" type="button" @click="openPanel('settle')">登记结算</button>
        <button class="btn" type="button" @click="openPanel('backfill')">存量回填</button>
        <button class="btn" type="button" @click="openPanel('rule')">核发规则</button>
        <button class="btn" type="button" @click="exportJson">导出对账文件(JSON)</button>
        <button class="btn" type="button" @click="exportCsv">下载对账CSV</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">累计核发绿证(个)</span>
        <strong class="stat-value">{{ balances['累计核发绿证'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">累计交割绿证(个)</span>
        <strong class="stat-value">{{ balances['累计交割绿证'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">绿证结余(个)</span>
        <strong class="stat-value">{{ balances['结余绿证'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">碳减排结余(tCO2e)</span>
        <strong class="stat-value">{{ balances['结余碳减排'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">口径指纹</span>
        <strong class="stat-value" style="font-size: 0.95rem">{{ caliber || '—' }}</strong>
      </article>
    </div>

    <!-- 操作面板 -->
    <div v-if="panel" class="ledger-panel">
      <div class="panel-head">
        <strong>{{ panelTitle }}</strong>
        <button class="link" type="button" @click="panel = ''">收起</button>
      </div>

      <!-- 月末核发与结算（一次事务） -->
      <form v-if="panel === 'close'" class="panel-body" @submit.prevent="submitClose">
        <div class="form-grid">
          <label><span>核发月份 (YYYY-MM)</span><input v-model="closeForm.月份" placeholder="2026-09" /></label>
          <label><span>运行月报编号</span><input v-model="closeForm.月报编号" placeholder="REPO-202609" /></label>
          <label><span>月报发电量 (MWh)</span><input v-model.number="closeForm.发电量MWh" type="number" /></label>
          <label><span>财务填报结余(个)</span><input v-model.number="closeForm.财务填报结余" type="number" placeholder="与台账对平用，可留空" /></label>
        </div>
        <div class="settle-lines">
          <div v-for="(line, idx) in closeForm.lines" :key="idx" class="settle-line">
            <input v-model="line.凭证号" placeholder="凭证号" />
            <input v-model="line.对手方" placeholder="对手方" />
            <input v-model.number="line.绿证交割量" type="number" placeholder="绿证交割量" />
            <input v-model.number="line.碳减排交割量" type="number" placeholder="碳减排交割量(t)" />
            <input v-model.number="line.结算金额" type="number" placeholder="结算金额(元)" />
            <button class="link" type="button" @click="closeForm.lines.splice(idx, 1)">删除</button>
          </div>
          <button class="btn ghost" type="button" @click="addCloseLine">+ 加一笔结算</button>
        </div>
        <p class="panel-tip">核发与结算在同一次事务写入，两边对不上整批失败、已全部回滚；重复凭证号只保留最初那条。</p>
        <button class="btn primary" type="submit">一次事务关账</button>
      </form>

      <!-- 单笔结算登记 -->
      <form v-else-if="panel === 'settle'" class="panel-body" @submit.prevent="submitSettlement">
        <div class="form-grid">
          <label><span>凭证号</span><input v-model="settleForm.凭证号" placeholder="JZ-202609-001" /></label>
          <label><span>归属月份</span><input v-model="settleForm.归属月份" placeholder="2026-09" /></label>
          <label><span>对手方</span><input v-model="settleForm.对手方" /></label>
          <label><span>交易日期</span><input v-model="settleForm.交易日期" placeholder="2026-09-30" /></label>
          <label><span>绿证交割量(个)</span><input v-model.number="settleForm.绿证交割量" type="number" /></label>
          <label><span>碳减排交割量(t)</span><input v-model.number="settleForm.碳减排交割量" type="number" /></label>
          <label><span>结算金额(元)</span><input v-model.number="settleForm.结算金额" type="number" /></label>
        </div>
        <p class="panel-tip">同一凭证号重复登记（含并发）只保留最初那条；关账后的月份不能补登记。</p>
        <button class="btn primary" type="submit">登记结算</button>
      </form>

      <!-- 存量回填 -->
      <form v-else-if="panel === 'backfill'" class="panel-body" @submit.prevent="submitBackfill">
        <textarea
          v-model="backfillText"
          rows="6"
          placeholder='每行一条 JSON，例如：&#10;{"核发月份":"2026-05","发电量(MWh)":4600,"月报编号":"REPO-202605"}'
        ></textarea>
        <p class="panel-tip">按核发月份匹配当时生效的规则版本，整批一次写入；任一月份不合法则整批失败。</p>
        <button class="btn primary" type="submit">整批回填</button>
      </form>

      <!-- 规则版本 -->
      <div v-else-if="panel === 'rule'" class="panel-body">
        <table class="data-table">
          <thead>
            <tr><th>规则版本</th><th>生效月份</th><th>绿证系数</th><th>减排因子</th><th>口径说明</th><th>登记时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="rule in rules" :key="String(rule['规则版本'])">
              <td>{{ rule.规则版本 }}</td><td>{{ rule.生效月份 }}</td>
              <td>{{ rule.绿证系数 }}</td><td>{{ rule.减排因子 }}</td>
              <td>{{ rule.核发口径说明 }}</td><td>{{ rule.登记时间 }}</td>
            </tr>
          </tbody>
        </table>
        <form class="form-grid" style="margin-top:12px" @submit.prevent="submitRule">
          <label><span>新版本号</span><input v-model="ruleForm.规则版本" placeholder="v2027" /></label>
          <label><span>生效月份</span><input v-model="ruleForm.生效月份" placeholder="2027-01" /></label>
          <label><span>绿证系数</span><input v-model.number="ruleForm.绿证系数" type="number" step="0.0001" /></label>
          <label><span>减排因子</span><input v-model.number="ruleForm.减排因子" type="number" step="0.0001" /></label>
          <button class="btn primary" type="submit">登记新版本</button>
        </form>
        <p class="panel-tip">调整规则只新增版本，仅对未核发月份生效；已核发月份永不重算，继续沿用原算法。</p>
      </div>
    </div>

    <form class="filter-bar" @submit.prevent="reload(1)">
      <label class="filter-item"><span>归属月份</span><input v-model="filters.month" placeholder="2026-09" /></label>
      <label class="filter-item">
        <span>类型</span>
        <select v-model="filters.type">
          <option value="">全部</option><option>核发</option><option>结算</option>
        </select>
      </label>
      <label class="filter-item"><span>关键字</span><input v-model="filters.keyword" placeholder="凭证号/核发编号/月报/对手方" /></label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置</button>
    </form>

    <table class="data-table">
      <thead>
        <tr><th v-for="column in columns" :key="column">{{ column }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row['台账编号'])">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length" class="empty-state">台账暂无数据，可先回填存量或做月末核发</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot ledger-foot">
      <span>共 {{ total }} 条 · 第 {{ page }} 页 · 全量口径指纹 {{ caliber || '—' }}</span>
      <span>
        <button class="link" type="button" :disabled="page <= 1" @click="reload(page - 1)">上一页</button>
        <button class="link" type="button" :disabled="rows.length < size" @click="reload(page + 1)">下一页</button>
      </span>
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/carbon-ledger'
const columns = [
  '台账编号', '类型', '归属月份', '凭证号/核发编号', '月报编号', '对手方',
  '绿证数量(个)', '碳减排量(t)', '结算金额(元)', '规则版本', '登记时间',
]

type Row = Record<string, string | number | null>
const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = 20
const caliber = ref('')
const balances = ref<Record<string, number>>({})
const rules = ref<Row[]>([])
const filters = reactive({ month: '', type: '', keyword: '' })
const message = ref('')
const messageOk = ref(true)

const panel = ref('')
const panelTitle = ref('')
const closeForm = reactive<{ 月份: string; 月报编号: string; 发电量MWh: number | null; 财务填报结余: number | null; lines: any[] }>(
  { 月份: '', 月报编号: '', 发电量MWh: null, 财务填报结余: null, lines: [] },
)
const settleForm = reactive({
  凭证号: '', 归属月份: '', 对手方: '', 交易日期: '',
  绿证交割量: 0, 碳减排交割量: 0, 结算金额: 0,
})
const ruleForm = reactive({ 规则版本: '', 生效月份: '', 绿证系数: 1, 减排因子: 0.5703 })
const backfillText = ref('')

function openPanel(name: string) {
  panel.value = name
  panelTitle.value = {
    close: '月末核发与结算（一次事务）',
    settle: '登记交易结算',
    backfill: '存量绿证按核发月份回填',
    rule: '核发规则版本',
  }[name] as string
  if (name === 'rule') {
    void loadRules()
  }
}

function addCloseLine() {
  closeForm.lines.push({ 凭证号: '', 对手方: '', 绿证交割量: 0, 碳减排交割量: 0, 结算金额: 0 })
}

function resetFilters() {
  filters.month = ''
  filters.type = ''
  filters.keyword = ''
  void reload(1)
}

function buildQuery(p: number) {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.type) params.set('type', filters.type)
  if (filters.keyword) params.set('keyword', filters.keyword)
  params.set('page', String(p))
  params.set('size', String(size))
  return params.toString()
}

async function reload(p = page.value) {
  try {
    const resp = await request(`${ENDPOINT}/entries?${buildQuery(p)}`)
    if (!resp.ok) throw new Error('台账列表读取失败')
    const data = await resp.json()
    rows.value = data.items ?? []
    total.value = data.total ?? 0
    page.value = p
    caliber.value = data.caliber ?? ''
    await loadSummary()
  } catch (error) {
    flash(error instanceof Error ? error.message : '台账列表读取失败', false)
  }
}

async function loadSummary() {
  const resp = await request(`${ENDPOINT}/summary`)
  if (resp.ok) {
    const data = await resp.json()
    balances.value = data.结余 ?? {}
  }
}

async function loadRules() {
  const resp = await request(`${ENDPOINT}/rules`)
  if (resp.ok) {
    rules.value = (await resp.json()).items ?? []
  }
}

function flash(text: string, ok: boolean) {
  message.value = text
  messageOk.value = ok
}

async function postJson(path: string, body: unknown) {
  const resp = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return resp.json()
}

async function submitClose() {
  const body = {
    月份: closeForm.月份,
    月报编号: closeForm.月报编号,
    发电量MWh: closeForm.发电量MWh,
    财务填报结余: closeForm.财务填报结余 ?? undefined,
    结算明细: closeForm.lines.filter((l) => l.凭证号),
  }
  const result = await postJson(`${ENDPOINT}/month-end-close`, body)
  flash(result.message, result.ok)
  if (result.ok) {
    panel.value = ''
    closeForm.lines = []
    await reload(1)
  }
}

async function submitSettlement() {
  const result = await postJson(`${ENDPOINT}/settlements`, { ...settleForm })
  flash(result.message, result.ok)
  if (result.ok) await reload(page.value)
}

async function submitBackfill() {
  let items: unknown[] = []
  try {
    items = backfillText.value
      .split('\n')
      .map((line: string) => line.trim())
      .filter(Boolean)
      .map((line: string) => JSON.parse(line))
  } catch {
    flash('回填内容必须是每行一条 JSON', false)
    return
  }
  const result = await postJson(`${ENDPOINT}/backfill`, { items })
  flash(result.message, result.ok)
  if (result.ok) {
    panel.value = ''
    await reload(1)
  }
}

async function submitRule() {
  const result = await postJson(`${ENDPOINT}/rules`, { ...ruleForm })
  flash(result.message, result.ok)
  if (result.ok) {
    await loadRules()
    await reload(page.value)
  }
}

function exportJson() {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.type) params.set('type', filters.type)
  if (filters.keyword) params.set('keyword', filters.keyword)
  window.open(`${ENDPOINT}/export?${params.toString()}`, '_blank')
}

function exportCsv() {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.type) params.set('type', filters.type)
  if (filters.keyword) params.set('keyword', filters.keyword)
  window.open(`${ENDPOINT}/export.csv?${params.toString()}`, '_blank')
}

onMounted(() => {
  addCloseLine()
  void reload(1)
})
</script>

<style scoped>
.page-actions {
  flex-wrap: wrap;
  gap: 8px;
}
.ledger-panel {
  border: 1px solid var(--border-color, #d8dee8);
  border-radius: 10px;
  padding: 14px 16px;
  margin: 12px 0;
  background: #fafbfd;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}
.form-grid label,
.settle-line input {
  font-size: 0.86rem;
}
.form-grid label span,
.filter-item span {
  display: block;
  color: #667085;
  margin-bottom: 4px;
}
input,
select,
textarea {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  font: inherit;
}
.settle-lines {
  margin: 12px 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.settle-line {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr 1.1fr 1fr auto;
  gap: 8px;
  align-items: center;
}
.panel-tip {
  color: #667085;
  font-size: 0.82rem;
  margin: 10px 0;
}
.ledger-foot {
  display: flex;
  gap: 16px;
  align-items: center;
  flex-wrap: wrap;
}
.ok-text {
  color: #027a48;
}
</style>
