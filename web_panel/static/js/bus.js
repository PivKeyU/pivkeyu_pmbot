/* 轻量事件总线：把 WebSocket 事件分发给当前页面。 */

const handlers = new Map();

export function on(type, handler) {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(handler);
  return () => off(type, handler);
}

export function off(type, handler) {
  const set = handlers.get(type);
  if (set) set.delete(handler);
}

export function emit(event) {
  if (!event || !event.type) return;
  for (const type of [event.type, '*']) {
    const set = handlers.get(type);
    if (!set) continue;
    for (const handler of [...set]) {
      try {
        handler(event);
      } catch (err) {
        console.error('事件处理出错', err);
      }
    }
  }
}
