// All clients independently decode the same live stream at YUI's head.
// Only the claimed controller / instance owner can change the shared URL or mute.
using UdonSharp;
using UnityEngine;
using UnityEngine.UI;
using VRC.SDKBase;
using VRC.SDK3.Components;
using VRC.SDK3.Components.Video;
using VRC.SDK3.Video.Components;
using VRC.SDK3.Video.Components.Base;
using VRC.SDK3.Video.Components.AVPro;
using TMPro;

[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class NekoNpcVoice : UdonSharpBehaviour
{
    public VRCAVProVideoPlayer videoPlayer;
    public AudioSource voiceSource;
    public NekoNpcTelemetry telemetry;
    public NekoMidiRouter router;
    public VRCUrl fixedStreamUrl = new VRCUrl("");
    public VRCUrlInputField urlInput;
    public TextMeshProUGUI statusText;
    public GameObject settingsBody;
    public TextMeshProUGUI settingsToggleText;
    public int maxReconnectAttempts = 0;
    [UdonSynced] private VRCUrl _sharedUrl = new VRCUrl("");
    [UdonSynced] private bool _muted;
    [UdonSynced] private int _revision;
    private int _appliedRevision = -1;
    private string _loadedUrl = "";
    private string _state = "disabled";
    private string _error;
    private bool _loaded;
    private int _retryCount;
    private float _lastLoad = -100f;
    private float _retryAt = -1f;
    private int _lastSpeech = -1;
    private VRCUrl _emptyUrl = new VRCUrl("");
    private float _nextAuthorityCheck;
    private float _loadStartedAt = -1f;
    private bool _settingsVisible = true;
    private bool _releasingInput;
    private int[] _cueSession = new int[16384];
    private int[] _cueText = new int[16384];
    private int[] _cueAction = new int[16384];
    private int[] _cueDelay = new int[16384];
    private int[] _cueDuration = new int[16384];

    void Start()
    {
        if (videoPlayer != null) { videoPlayer.Loop = false; videoPlayer.EnableAutomaticResync = false; }
        EndUrlEdit();
        ApplySharedState();
    }

    public bool IsConfigured()
    {
        // Advertise installed hardware before a URL is entered, so discovery does
        // not need to be repeated when the user connects the stream later.
        return videoPlayer != null && voiceSource != null;
    }

    public bool IsGloballyMuted() { return _muted; }

    private VRCUrl EffectiveUrl()
    {
        VRCUrl candidate = _sharedUrl != null && _sharedUrl.Get().Length > 0 ? _sharedUrl : fixedStreamUrl;
        // A local test fallback must never open another visitor's own localhost service.
        if (candidate != null && candidate.Get().StartsWith("http://127.0.0.1:17864/"))
        {
            VRCPlayerApi local = Networking.LocalPlayer;
            if (local != null && !local.isInstanceOwner && (router == null || !router.IsLocalClaimedDriver())) return _emptyUrl;
        }
        return candidate;
    }

    public bool IsAllowedUrl(VRCUrl value)
    {
        if (value == null) return false;
        string text = value.Get();
        if (text == null || text.Length == 0 || text.Length > 2048) return false;
        return (text.StartsWith("https://") || text.StartsWith("http://127.0.0.1:17864/"))
            && text.EndsWith(".m3u8");
    }

    private bool MayConfigure(VRCPlayerApi local)
    {
        return local != null && (local.isInstanceOwner || (router != null && router.HasLocalDriverAuthority()));
    }

    public override bool OnOwnershipRequest(VRCPlayerApi requester, VRCPlayerApi requestedOwner)
    {
        return requestedOwner != null && (requestedOwner.isInstanceOwner
            || (router != null && requestedOwner.playerId == router.GetClaimedDriverPid()));
    }

    public void ApplyUrl()
    {
        // Keep the pasted value, then return the keyboard before any early exit
        // (including a rejected URL or a visitor without configuration rights).
        VRCUrl next = urlInput == null ? _emptyUrl : urlInput.GetUrl();
        EndUrlEdit();
        VRCPlayerApi local = Networking.LocalPlayer;
        if (!MayConfigure(local) || urlInput == null) { SetPanel("仅控制者或实例所有者可以连接语音。"); return; }
        if (!IsAllowedUrl(next)) { SetPanel("请粘贴完整的 HTTPS 地址，结尾应为 /yui/live.m3u8。"); return; }
        if (next.Get() == _loadedUrl && (_state == "loading" || _state == "playing"))
        { SetPanel(_state == "loading" ? "正在连接，请等待。" : "语音已连接。"); return; }
        if (Time.timeSinceLevelLoad < _lastLoad + 5.2f) { SetPanel("请稍候几秒再连接，避免重复加载。"); return; }
        if (!Networking.IsOwner(gameObject)) Networking.SetOwner(local, gameObject);
        if (!Networking.IsOwner(gameObject)) { SetPanel("暂时无法取得语音控制权，请稍后再试。"); return; }
        _sharedUrl = next; _revision++; _retryCount = 0; _retryAt = -1f;
        RequestSerialization(); ApplySharedState();
    }

    public void ToggleMute()
    {
        EndUrlEdit();
        VRCPlayerApi local = Networking.LocalPlayer;
        if (!MayConfigure(local)) return;
        if (!Networking.IsOwner(gameObject)) Networking.SetOwner(local, gameObject);
        if (!Networking.IsOwner(gameObject)) return;
        _muted = !_muted; RequestSerialization(); ApplySharedState();
        SetState(_state, _error);
    }

    public void MuteForSafety()
    {
        if (voiceSource != null) voiceSource.mute = true;
        VRCPlayerApi local = Networking.LocalPlayer;
        if (!MayConfigure(local)) return;
        if (!Networking.IsOwner(gameObject)) Networking.SetOwner(local, gameObject);
        if (Networking.IsOwner(gameObject)) { _muted = true; RequestSerialization(); SetState(_state, _error); }
    }

    public void RetryVoice()
    {
        EndUrlEdit();
        if (_state == "loading") { SetPanel("正在连接，请等待；超时后可重新连接。"); return; }
        if (!IsAllowedUrl(EffectiveUrl())) { SetPanel("请先粘贴联机语音地址并点击连接语音。"); return; }
        _retryCount = 0; _retryAt = Mathf.Max(Time.timeSinceLevelLoad, _lastLoad + 5.2f);
        SetPanel("准备重新连接语音……");
    }

    public void ToggleSettings()
    {
        EndUrlEdit();
        _settingsVisible = !_settingsVisible;
        if (settingsBody != null) settingsBody.SetActive(_settingsVisible);
        if (settingsToggleText != null) settingsToggleText.text = _settingsVisible ? "关闭设置" : "打开设置";
    }

    // Also wired to URL end-edit. Deactivation can invoke end-edit again.
    public void EndUrlEdit()
    {
        if (_releasingInput || urlInput == null) return;
        _releasingInput = true;
        urlInput.DeactivateInputField();
        // Selectable clears its own EventSystem selection when disabled for
        // interaction. Restore editability immediately; never select another UI.
        bool editable = urlInput.interactable;
        urlInput.interactable = false;
        urlInput.interactable = editable;
        _releasingInput = false;
    }

    void OnDisable() { EndUrlEdit(); }

    public override void OnDeserialization() { ApplySharedState(); }

    private void ApplySharedState()
    {
        if (voiceSource != null) voiceSource.mute = _muted;
        VRCUrl next = EffectiveUrl();
        string text = next == null ? "" : next.Get();
        if (_appliedRevision != _revision || text != _loadedUrl)
        {
            _appliedRevision = _revision; _retryCount = 0;
            _retryAt = IsAllowedUrl(next) ? Mathf.Max(Time.timeSinceLevelLoad, _lastLoad + 5.2f) : -1f;
        }
        SetPanel(_muted ? "YUI 语音：全局静音" : !IsAllowedUrl(next) ? "请粘贴联机地址并连接语音。" : "YUI 语音：" + _state);
    }

    void Update()
    {
        if (_state == "loading" && _loadStartedAt >= 0f && Time.timeSinceLevelLoad - _loadStartedAt > 20f)
        {
            _retryAt = -1f; SetState("error", "load_timeout");
            SetPanel("连接超时。请检查地址和允许不受信任 URL 设置，再手动重试。");
        }
        if (Time.timeSinceLevelLoad >= _nextAuthorityCheck)
        {
            _nextAuthorityCheck = Time.timeSinceLevelLoad + 1f;
            if (_state == "disabled" && _retryAt < 0f && IsConfigured() && IsAllowedUrl(EffectiveUrl()))
                _retryAt = Mathf.Max(Time.timeSinceLevelLoad, _lastLoad + 5.2f);
        }
        if (_retryAt < 0f || Time.timeSinceLevelLoad < _retryAt) return;
        _retryAt = -1f;
        VRCUrl next = EffectiveUrl();
        if (!IsAllowedUrl(next) || videoPlayer == null || voiceSource == null)
        {
            SetState("disabled", null); SetPanel("请粘贴联机语音地址，再点击连接语音。"); return;
        }
        _lastLoad = Time.timeSinceLevelLoad; _loadedUrl = next.Get(); _loaded = true;
        _loadStartedAt = Time.timeSinceLevelLoad;
        SetState("loading", null); videoPlayer.LoadURL(next);
    }

    public override void OnVideoReady() { if (videoPlayer != null) videoPlayer.Play(); }
    public override void OnVideoStart() { _retryCount = 0; SetState("playing", null); }
    public override void OnVideoEnd() { SetState("error", "stream_ended"); ScheduleRetry(); }
    public override void OnVideoError(VideoError videoError)
    {
        SetState("error", videoError.ToString());
        // AccessDenied normally requires the viewer's Allow Untrusted URLs setting.
        if (videoError == VideoError.AccessDenied) { SetPanel("请在 VRChat 开启 Allow Untrusted URLs，再重新连接。"); return; }
        ScheduleRetry();
    }

    private void ScheduleRetry()
    {
        if (_retryCount >= maxReconnectAttempts) { SetPanel("语音连接失败（" + _error + "）。请检查地址，再手动重新连接。"); return; }
        _retryCount++; _retryAt = Time.timeSinceLevelLoad + 5.2f * _retryCount;
    }

    private void SetPanel(string message) { if (statusText != null) statusText.text = message; }
    private void SetState(string state, string error)
    {
        _state = state; _error = error;
        SetPanel(_muted ? "YUI 语音：全局静音" : "YUI 语音：" + state + (error == null ? "" : " / " + error));
        if (telemetry != null && router != null && router.GetSession() > 0 && router.HasLocalDriverAuthority())
            telemetry.Emit("voice.state", "\"state\":" + telemetry.J(_muted && state == "playing" ? "paused" : state) + ",\"reason\":\"video_event\",\"error_code\":"
                + (error == null ? "null" : telemetry.J(error)) + ",\"url_loaded\":" + telemetry.B(_loaded));
    }

    public string SnapshotJson()
    {
        return "{\"state\":" + telemetry.J(IsConfigured() ? (_muted && _state == "playing" ? "paused" : _state) : "disabled")
            + ",\"error_code\":" + (_error == null ? "null" : telemetry.J(_error))
            + ",\"url_loaded\":" + telemetry.B(_loaded) + ",\"last_speech_seq\":"
            + (_lastSpeech < 0 ? "null" : _lastSpeech.ToString()) + "}";
    }

    public string ValidateCue(int speech, int text, int action, int delay, int duration)
    {
        if (!IsConfigured() || _state != "playing" || _muted) return "voice_unavailable";
        if (speech < 1 || speech > 16383 || duration < 1 || duration > 127) return "invalid_param";
        int session = router.GetSession();
        if (_cueSession[speech] == session && (_cueText[speech] != text || _cueAction[speech] != action
            || _cueDelay[speech] != delay || _cueDuration[speech] != duration)) return "speech_seq_conflict";
        return null;
    }

    public void CommitCue(int speech, int text, int action, int delay, int duration)
    {
        int session = router.GetSession();
        if (_cueSession[speech] == session) return;
        _cueSession[speech] = session; _cueText[speech] = text; _cueAction[speech] = action;
        _cueDelay[speech] = delay; _cueDuration[speech] = duration; _lastSpeech = speech;
        telemetry.Emit("voice.cue", "\"speech_seq\":" + speech + ",\"text_transfer_seq\":" + text + ",\"action_seq\":" + action
            + ",\"estimated_delay_ms\":" + (delay * 100) + ",\"duration_ms\":" + (duration * 250));
    }
}
