/* API 客户端：统一封装 fetch、错误与未登录处理、WebSocket 实时通道。 */

const PANEL_HEADER = 'PMBotPanel';

let unauthorizedHandler = null;
let miniAppToken = '';

// Mini App 页面的 body 带 miniapp 类；页面据此隐藏这个端口上没有开放的功能
export const isMiniApp = document.body.classList.contains('miniapp');

export function setMiniAppToken(token) {
  miniAppToken = token;
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler;
}

async function request(method, path, body) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-Requested-With'] = PANEL_HEADER;
  if (miniAppToken) headers['X-MiniApp-Token'] = miniAppToken;

  const response = await fetch(`${isMiniApp ? '/miniapp' : ''}${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    payload = null;
  }

  if (response.status === 401) {
    if (unauthorizedHandler) unauthorizedHandler();
    throw new ApiError((payload && payload.error) || '未登录或会话已过期', 401);
  }
  if (!response.ok || (payload && payload.ok === false)) {
    throw new ApiError((payload && payload.error) || `请求失败（HTTP ${response.status}）`, response.status);
  }
  return payload ? payload.data : null;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body = {}) => request('POST', path, body),
  put: (path, body = {}) => request('PUT', path, body),
  del: (path, body) => request('DELETE', path, body),
};

export function connectEvents({ onEvent, onStatus }) {
  let socket = null;
  let heartbeat = null;
  let retryTimer = null;
  let retry = 0;
  let closed = false;
  let hasConnectedOnce = false;

  const open = () => {
    if (closed) return;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${protocol}//${location.host}/ws`);

    socket.onopen = () => {
      const wasReconnect = hasConnectedOnce;
      hasConnectedOnce = true;
      retry = 0;
      if (onStatus) onStatus(true);
      if (wasReconnect && onEvent) {
        onEvent({ type: 'ws_reconnected', ts: Date.now() / 1000 });
      }
      heartbeat = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send('ping');
      }, 25000);
    };
    socket.onmessage = (event) => {
      try {
        onEvent(JSON.parse(event.data));
      } catch (err) {
        /* 忽略无法解析的帧 */
      }
    };
    socket.onclose = () => {
      clearInterval(heartbeat);
      heartbeat = null;
      if (onStatus) onStatus(false);
      if (!closed) {
        const delay = Math.min(15000, 1000 * 2 ** Math.min(retry++, 4));
        clearTimeout(retryTimer);
        retryTimer = setTimeout(open, delay);
      }
    };
    socket.onerror = () => socket && socket.close();
  };

  open();
  return {
    close() {
      closed = true;
      clearInterval(heartbeat);
      clearTimeout(retryTimer);
      if (socket) socket.close();
    },
  };
}
