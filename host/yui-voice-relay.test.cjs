const test = require('node:test');
const assert = require('node:assert/strict');
const {pcmFrame, createRelay} = require('./yui-voice-relay.js');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function buffer(samples, rate=48000, other=null) {
    const channels=[Float32Array.from(samples)]; if(other) channels.push(Float32Array.from(other));
    return { length:samples.length,sampleRate:rate,numberOfChannels:channels.length,duration:samples.length/rate,getChannelData:i=>channels[i] };
}
function setup(fetcher) {
    const calls=[];
    const env={fetch:async (url,options={})=> {
        calls.push({url,options});
        if(fetcher) return fetcher(url,options,calls);
        return {ok:true,status:200,json:async()=>url.endsWith('/health')?{state:'streaming',armed:true}:{status:'accepted',queued_ms:0}};
    },setTimeout:()=>0,clearTimeout:()=>{},AbortController,AbortSignal,crypto:require('node:crypto').webcrypto,lanlan_config:{lanlan_name:'YUI'}};
    const relay=createRelay({endpoint:'http://127.0.0.1:17863',token:'test-private',character:'YUI',sender:'a'.repeat(32)},env);
    return {relay,env,calls};
}
test('PCM conversion resamples, downmixes and clips without exposing NaNs',()=> {
    const result=pcmFrame(buffer([1,1,-1,-1,NaN,NaN],48000),0,3);
    const view=new DataView(result.buffer);
    assert.equal(view.getInt16(0,true),32767); assert.equal(view.getInt16(2,true),-32768); assert.equal(view.getInt16(4,true),0);
    const stereo=new DataView(pcmFrame(buffer([1,1],24000,[-1,-1]),0,2).buffer);
    assert.equal(stereo.getInt16(0,true),0);
});
test('unknown or another character is never forwarded',async()=> {
    const {relay,env,calls}=setup(); await relay.poll();
    env.lanlan_config={lanlan_name:'Another'};
    assert.equal(relay.enqueue({buffer:buffer([.5,.5],24000)}),false);
    assert.equal(calls.filter(c=>c.url.endsWith('/audio')).length,0);
    env.lanlan_config={}; assert.equal(relay.enqueue({buffer:buffer([.5],24000)}),false);
    relay.close();
});
test('audio is chunked and posted in order after an authenticated reset',async()=> {
    const {relay,calls}=setup(); await relay.poll();
    assert.equal(relay.enqueue({buffer:buffer(Array(6000).fill(.5),24000)}),true);
    await pause(20);
    const audio=calls.filter(c=>c.url.endsWith('/audio'));
    assert.deepEqual(audio.map(c=>c.options.headers['X-Yui-Sequence']),['0','1','2']);
    assert.deepEqual(audio.map(c=>c.options.body.byteLength),[4800,4800,2400]);
    assert.equal(audio[0].options.headers['X-Yui-Token'],'test-private'); relay.close();
});
test('reset fences an in-flight old packet and discards remaining cancelled speech',async()=> {
    let release;
    const {relay,calls}=setup(async(url,options)=> {
        if(url.endsWith('/audio')) await new Promise(resolve=>release=resolve);
        return {ok:true,status:200,json:async()=>url.endsWith('/health')?{state:'streaming',armed:true}:{status:'accepted',queued_ms:0}};
    });
    await relay.poll(); relay.enqueue({buffer:buffer(Array(12000).fill(.5),24000)});
    await pause(5); relay.reset(); release(); await pause(10);
    assert.equal(calls.filter(c=>c.url.endsWith('/audio')).length,1);
    const resets=calls.filter(c=>c.url.endsWith('/reset'));
    assert.equal(resets.at(-1).options.headers['X-Yui-Epoch'],'1'); relay.close();
});
test('encoder failure leaves subsequent buffers on normal desktop playback',async()=> {
    const {relay}=setup(async(url)=>({ok:true,status:url.endsWith('/audio')?503:200,json:async()=>url.endsWith('/health')?{state:'streaming',armed:true}:{error:'encoder_not_ready'}}));
    await relay.poll(); relay.enqueue({buffer:buffer([.5,.5],24000)}); await pause(10);
    assert.equal(relay.enqueue({buffer:buffer([.5,.5],24000)}),false); relay.close();
});

test('a formerly ready address is cleared when health fails or stops being ready',async()=> {
    let health={state:'streaming',armed:false,tunnel_state:'ready',public_url:'https://voice.example/yui/live.m3u8'};
    let failure=false, ok=true;
    const {relay}=setup(async()=> {
        if(failure) throw new Error('offline');
        return {ok,status:ok?200:503,json:async()=>health};
    });
    await relay.poll(); assert.equal(relay.publicUrl,health.public_url);
    failure=true; await relay.poll(); assert.equal(relay.publicUrl,null);
    failure=false; await relay.poll(); assert.equal(relay.publicUrl,health.public_url);
    ok=false; await relay.poll(); assert.equal(relay.publicUrl,null);
    ok=true; health={...health,tunnel_state:'unavailable',public_probe_error:'http_530'};
    await relay.poll(); assert.equal(relay.publicUrl,null);
    assert.match(relay.status().state,/530/);
    health={...health,tunnel_state:'ready',public_probe_error:null};
    await relay.poll(); assert.equal(relay.publicUrl,health.public_url);
    relay.close(); assert.equal(relay.publicUrl,null);
});

test('closing during a pending health request cannot restore an address or audio forwarding',async()=> {
    let release;
    const {relay}=setup(async()=>new Promise(resolve=>release=resolve));
    const pending=relay.poll(); relay.close();
    release({ok:true,status:200,json:async()=>({state:'streaming',armed:true,tunnel_state:'ready',public_url:'https://voice.example/yui/live.m3u8'})});
    await pending;
    assert.equal(relay.publicUrl,null); assert.equal(relay.status().armed,false);
    assert.equal(relay.enqueue({buffer:buffer([.5,.5],24000)}),false);
});
