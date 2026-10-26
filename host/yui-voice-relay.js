/* TTS-only bridge. This module never opens a microphone or captures desktop audio. */
(function (root) {
    'use strict';
    const RATE = 24000;
    function pcmFrame(buffer, start, count) {
        const result = new Uint8Array(count * 2), view = new DataView(result.buffer);
        const ratio = buffer.sampleRate / RATE;
        for (let i = 0; i < count; i++) {
            const position = (start + i) * ratio;
            const left = Math.min(Math.floor(position), buffer.length - 1);
            const right = Math.min(left + 1, buffer.length - 1);
            let value = 0;
            for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
                const samples = buffer.getChannelData(channel);
                value += samples[left] + (samples[right] - samples[left]) * (position - left);
            }
            value /= buffer.numberOfChannels;
            if (!Number.isFinite(value)) value = 0;
            value = Math.max(-1, Math.min(1, value));
            view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
        }
        return result;
    }
    function createRelay(config, environment) {
        const env = environment || root;
        const fetcher = env.fetch.bind(env);
        const wait = ms => new Promise(resolve => env.setTimeout(resolve, ms));
        const sender = config.sender || Array.from(env.crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
        let armed = false, epoch = 0, sequence = 0, pendingReset = true;
        let queue = [], pumping = false, stopped = false, lastError = null, publicStatus = null;
        const relay = { monitorLocal: false, publicUrl: null };
        function characterMatches() {
            const current = (env.lanlan_config || {}).lanlan_name;
            return typeof current === 'string' && current.trim().toLowerCase() === config.character.trim().toLowerCase();
        }
        function status() {
            return { armed: armed && characterMatches(), state: lastError || publicStatus || (armed ? '已连接 YUI 世界音源' : '等待世界播放器'), queuedBuffers: queue.length };
        }
        function announce() {
            if (typeof config.onStatus === 'function') config.onStatus(status());
        }
        async function post(path, data, currentEpoch, currentSequence) {
            const abort = new env.AbortController();
            const timer = env.setTimeout(() => abort.abort(), 1500);
            try {
                const response = await fetcher(config.endpoint + path, {
                    method: 'POST', mode: 'cors', credentials: 'omit', cache: 'no-store', signal: abort.signal,
                    headers: { 'Content-Type': 'application/octet-stream', 'X-Yui-Token': config.token,
                        'X-Yui-Sender': sender, 'X-Yui-Epoch': String(currentEpoch), 'X-Yui-Sequence': String(currentSequence) },
                    body: data || new Uint8Array(0)
                });
                return { code: response.status, body: await response.json() };
            } finally { env.clearTimeout(timer); }
        }
        function reset() {
            epoch++; sequence = 0; queue = []; pendingReset = true;
            pump();
        }
        async function pump() {
            if (pumping || stopped || !armed || !characterMatches()) return;
            pumping = true;
            try {
                while (armed && !stopped && characterMatches() && (pendingReset || queue.length)) {
                    const currentEpoch = epoch;
                    if (pendingReset) {
                        const result = await post('/reset', null, currentEpoch, 0);
                        if (currentEpoch !== epoch) continue;
                        if (result.code !== 200) throw new Error(result.body.error || result.body.status || 'reset_failed');
                        pendingReset = false;
                        continue;
                    }
                    const item = queue[0];
                    const total = Math.floor(item.buffer.length * RATE / item.buffer.sampleRate);
                    const count = Math.min(2400, total - item.offset);
                    if (count <= 0) { queue.shift(); continue; }
                    const data = pcmFrame(item.buffer, item.offset, count);
                    let result, attempts = 0;
                    do {
                        result = await post('/audio', data, currentEpoch, sequence);
                        if (currentEpoch !== epoch) break;
                        if (result.code === 429) await wait(100);
                    } while (result.code === 429 && ++attempts < 20 && currentEpoch === epoch && !stopped);
                    if (currentEpoch !== epoch) continue;
                    if (result.code !== 200) throw new Error(result.body.error || result.body.status || 'send_failed');
                    sequence++; item.offset += count;
                    if (item.offset >= total) queue.shift();
                    if (result.body.queued_ms >= 300) await wait(100);
                }
            } catch (error) {
                armed = false; queue = []; epoch++; sequence = 0; pendingReset = true;
                lastError = '语音转发暂停，桌面播放已保留';
                announce();
            } finally { pumping = false; }
        }
        function enqueue(item) {
            if (!armed || stopped || !characterMatches() || !item || !item.buffer) return false;
            const buffer = item.buffer;
            if (!(buffer.length > 0) || !(buffer.sampleRate >= 8000 && buffer.sampleRate <= 192000)
                || !(buffer.numberOfChannels >= 1 && buffer.numberOfChannels <= 8)) return false;
            const duration = queue.reduce((sum, entry) => sum + entry.buffer.duration - entry.offset / RATE, 0) + buffer.duration;
            if (duration > 60 || queue.length >= 128) {
                reset(); armed = false; lastError = '语音积压过长，已停止转发'; announce(); return false;
            }
            queue.push({ buffer, offset: 0 }); pump(); return true;
        }
        async function poll() {
            if (stopped) return;
            try {
                const response = await fetcher(config.endpoint + '/health', {
                    cache: 'no-store', mode: 'cors', credentials: 'omit', signal: env.AbortSignal.timeout(1500)
                });
                const health = await response.json();
                if (stopped) return;
                if (!response.ok) throw new Error('health_failed');
                relay.publicUrl = health.state === 'streaming' && health.tunnel_state === 'ready'
                    && typeof health.public_url === 'string' ? health.public_url : null;
                publicStatus = null;
                if (health.tunnel_state === 'probing' || health.tunnel_state === 'starting')
                    publicStatus = '正在验证公网语音地址，请稍候';
                else if (health.tunnel_state === 'unavailable') {
                    const code = /^http_[0-9]{3}$/.test(health.public_probe_error || '')
                        ? '（HTTP ' + health.public_probe_error.slice(5) + '）' : '';
                    publicStatus = '公网语音暂不可达' + code + '，正在重试';
                } else if (health.tunnel_state === 'failed') publicStatus = '语音隧道已断开，请检查网络';
                const next = response.ok && health.state === 'streaming' && health.armed === true;
                if (!next && armed) reset();
                armed = next;
                lastError = null;
                if (next) pump();
            } catch (_) {
                if (stopped) return;
                relay.publicUrl = null; publicStatus = null;
                if (armed) reset(); armed = false; lastError = '语音服务未启动';
            }
            announce();
            if (!stopped) env.setTimeout(poll, 1000);
        }
        relay.enqueue = enqueue; relay.reset = reset; relay.status = status; relay.poll = poll;
        relay.close = function () { stopped = true; reset(); armed = false; relay.publicUrl = null; };
        return relay;
    }
    if (typeof module === 'object' && module.exports) module.exports = { pcmFrame, createRelay };
    if (!root || !root.document || !root.nekoYuiVoiceConfig || root.nekoYuiVoice) return;
    const config = root.nekoYuiVoiceConfig;
    let label;
    config.onStatus = state => { if (label) label.textContent = 'YUI 世界语音：' + state.state; };
    const relay = createRelay(config, root);
    root.nekoYuiVoice = relay;
    root.addEventListener('neko-assistant-speech-cancel', relay.reset);
    root.addEventListener('beforeunload', relay.close);
    const showStatus = () => {
        const box = root.document.createElement('div');
        box.id = 'neko-yui-world-voice-status';
        box.style.cssText = 'position:fixed;bottom:8px;left:8px;z-index:2147483000;background:#192332e8;color:#fff;border-radius:8px;padding:8px 10px;font:12px sans-serif;max-width:280px;pointer-events:auto';
        const reopen = root.document.createElement('button');
        reopen.id = 'neko-yui-world-voice-reopen'; reopen.textContent = 'YUI 语音';
        reopen.style.cssText = 'display:none;position:fixed;bottom:8px;left:8px;z-index:2147483000;padding:6px 10px;pointer-events:auto';
        const close = root.document.createElement('button');
        close.id = 'neko-yui-world-voice-close'; close.textContent = '×';
        close.setAttribute('aria-label', '收起 YUI 世界语音状态条');
        close.title = '收起状态条，语音继续运行';
        close.style.cssText = 'float:right;margin-left:8px;padding:0 5px;font-size:18px;line-height:18px;cursor:pointer';
        function hideStatus(hidden) {
            box.style.display = hidden ? 'none' : 'block';
            reopen.style.display = hidden ? 'block' : 'none';
            try { root.localStorage.setItem('nekoYuiVoiceStatusHidden', hidden ? '1' : '0'); } catch (_) {}
        }
        close.addEventListener('click', () => hideStatus(true));
        reopen.addEventListener('click', () => hideStatus(false));
        label = root.document.createElement('div'); label.textContent = 'YUI 世界语音：连接中';
        const monitor = root.document.createElement('label'); monitor.style.display = 'block';
        const checkbox = root.document.createElement('input'); checkbox.type = 'checkbox';
        checkbox.addEventListener('change', () => { relay.monitorLocal = checkbox.checked; });
        monitor.append(checkbox, root.document.createTextNode('同时在桌面试听'));
        const copy = root.document.createElement('button'); copy.textContent = '复制联机语音地址';
        copy.addEventListener('click', async () => {
            if (!relay.publicUrl) { label.textContent = '联机地址尚未准备好，请稍后重试'; return; }
            try { await root.navigator.clipboard.writeText(relay.publicUrl); label.textContent = '地址已复制，在世界语音面板粘贴并连接'; }
            catch (_) { root.prompt('复制此地址，在世界语音面板粘贴：',relay.publicUrl); }
        });
        box.append(close, label, monitor, copy); root.document.body.append(box, reopen);
        try { hideStatus(root.localStorage.getItem('nekoYuiVoiceStatusHidden') === '1'); } catch (_) {}
    };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', showStatus, {once:true});
    else showStatus();
    relay.poll();
})(typeof window !== 'undefined' ? window : null);
