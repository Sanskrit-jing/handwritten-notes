/** 服务端 API 封装：非 2xx 抛出 detail 消息。 */

async function handle(res) {
  if (!res.ok) {
    let msg = `请求失败（${res.status}）`
    try {
      const body = await res.json()
      if (body.detail) msg = typeof body.detail === 'string'
        ? body.detail : JSON.stringify(body.detail)
    } catch { /* 保留默认消息 */ }
    throw new Error(msg)
  }
  return res.json()
}

export function getMeta() {
  return fetch('/api/meta').then(handle)
}

export function postConfig(patch) {
  return fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }).then(handle)
}

export function postText(text) {
  return fetch('/api/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  }).then(handle)
}

export function postDocx(file) {
  const fd = new FormData()
  fd.append('file', file)
  return fetch('/api/docx', { method: 'POST', body: fd }).then(handle)
}

export function postFont(file) {
  const fd = new FormData()
  fd.append('file', file)
  return fetch('/api/font', { method: 'POST', body: fd }).then(handle)
}

export function postBg(file) {
  const fd = new FormData()
  fd.append('file', file)
  return fetch('/api/bg', { method: 'POST', body: fd }).then(handle)
}

export function deleteBg() {
  return fetch('/api/bg', { method: 'DELETE' }).then(handle)
}

export function pageUrl(page, tick) {
  return `/api/page/${page}?v=${tick}`
}
