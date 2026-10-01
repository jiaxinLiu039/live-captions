/* Shared subtitle version checks and end-of-audio handshake. */
globalThis.CaptionState = {
  accept(state, message) {
    const version = message.version ?? 0;
    if (version < (state.version ?? 0)) return false;
    if (version > (state.version ?? 0)) {
      state.version = version;
      state.final = false;
      state.translationError = '';
    }
    return true;
  },
  result(state, message) {
    if (!this.accept(state, message)) return false;
    if (message.en !== undefined) {
      if (state.en !== message.en) state.final = false;
      state.en = message.en;
    }
    if (message.zh !== undefined) state.zh = message.zh;
    if (message.order !== undefined) state.order = message.order;
    if (message.sessionOrder !== undefined) state.sessionOrder = message.sessionOrder;
    if (message.start !== undefined && state.start == null) state.start = message.start;
    if (message.en_final !== undefined) state.en_final = message.en_final;
    if (message.zh_final === true) state.final = true;
    return true;
  },
  translation(state, message) {
    if (!this.accept(state, message)) return false;
    if (state.final && !message.final) return false;
    if (message.type === 'translation_error') {
      state.final = false;
      state.translationError = message.msg;
      return true;
    }
    // Retain the previous draft while waiting for the new request's first token.
    if (message.state !== 'pending') state.zh = message.accumulated;
    state.final = Boolean(message.final);
    state.translationError = '';
    return true;
  },
  bindSocket(socket, handler, isCurrent) {
    socket.onmessage = event => {
      if (!isCurrent()) return;
      const message = JSON.parse(event.data);
      if (message.type === 'config') {
        socket.captionSessionId = message.session_id;
        socket.captionSessionOrder = socket.captionSessionOrder ?? (this.sessionSequence = (this.sessionSequence ?? 0) + 1);
        socket.captionStopTimeout = message.stop_timeout_ms ?? 17000;
      }
      if (message.type === 'ready_to_stop' && socket.captionStopResolve) {
        socket.captionStopResolve(message);
      }
      // Provider line ids restart after reconnecting. Scope them to the connection.
      if (socket.captionSessionId) {
        message.sessionOrder = socket.captionSessionOrder;
        for (const key of ['sentence_id', 'line_id']) {
          if (message[key] !== undefined) message[key] = `${socket.captionSessionId}:${message[key]}`;
        }
      }
      handler(message);
    };
  },
  compare(a, b) {
    return (a.sessionOrder ?? 0) - (b.sessionOrder ?? 0)
      || (a.order?.[0] ?? 0) - (b.order?.[0] ?? 0)
      || (a.order?.[1] ?? 0) - (b.order?.[1] ?? 0);
  },
  drainSocket(socket) {
    if (!socket || socket.readyState !== 1) {
      return Promise.resolve({complete: false, reason: '连接已断开'});
    }
    return new Promise(resolve => {
      let settled = false;
      let timer;
      const closed = () => finish({complete: false, reason: '连接提前断开'});
      const finish = result => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        socket.removeEventListener('close', closed);
        socket.captionStopResolve = null;
        socket.close();
        resolve(result);
      };
      socket.captionStopResolve = message => finish({complete: message.complete !== false,
        reason: message.complete === false ? '部分字幕未完成' : ''});
      socket.addEventListener('close', closed);
      timer = setTimeout(() => finish({complete: false, reason: '等待最后译文超时'}),
        socket.captionStopTimeout ?? 17000);
      try { socket.send(new ArrayBuffer(0)); }
      catch { finish({complete: false, reason: '结束信号发送失败'}); }
    });
  }
};
