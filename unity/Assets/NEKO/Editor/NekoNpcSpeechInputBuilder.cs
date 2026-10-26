#if UNITY_EDITOR
using System;
using System.Linq;
using System.Reflection;
using TMPro;
using UdonSharp;
using UdonSharpEditor;
using UnityEditor;
using UnityEditor.Events;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;
using VRC.SDKBase;
using VRC.SDK3.Components;
using VRC.Udon;

public static class NekoNpcSpeechInputBuilder
{
    public static void EnsureSpeechInputProgram()
    {
        const string path = "Assets/NEKO/Npc/NekoNpcSpeechInput.asset";
        var program = AssetDatabase.LoadAssetAtPath<UdonSharpProgramAsset>(path);
        if (program == null)
        {
            program = ScriptableObject.CreateInstance<UdonSharpProgramAsset>();
            program.sourceCsScript = AssetDatabase.LoadAssetAtPath<MonoScript>("Assets/NEKO/Npc/NekoNpcSpeechInput.cs");
            if (program.sourceCsScript == null) throw new InvalidOperationException("Import the speech-input C# script first.");
            AssetDatabase.CreateAsset(program, path); AssetDatabase.SaveAssets();
            typeof(UdonSharpProgramAsset).GetMethod("ClearProgramAssetCache", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
        }
        if (program.ScriptVersion < UdonSharpProgramVersion.CurrentVersion)
        { program.ScriptVersion = UdonSharpProgramVersion.CurrentVersion; EditorUtility.SetDirty(program); AssetDatabase.SaveAssets(); }
    }

    [MenuItem("NEKO/Voice/Configure YUI Remote Microphone Input")]
    public static void Configure()
    {
        var scene = EditorSceneManager.GetActiveScene();
        if (EditorApplication.isPlaying || (scene.path != "Assets/SAVE.unity" && scene.path != "Assets/SAVE_YUI_VoiceTest.unity"))
            throw new InvalidOperationException("Open SAVE or SAVE_YUI_VoiceTest in edit mode first.");
        var router = UnityEngine.Object.FindObjectOfType<NekoMidiRouter>();
        if (router == null || router.locomotion == null || router.locomotion.npcRoot == null || router.perception == null || router.telemetry == null)
            throw new InvalidOperationException("YUI router/root/perception/telemetry is missing.");
        var font = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/NEKO/Fonts/NotoSansSC-Dynamic SDF.asset");
        if (font == null) throw new InvalidOperationException("The existing Chinese UI font is missing.");
        EnsureSpeechInputProgram();
        var root = router.locomotion.npcRoot;
        var authoritySync = root.GetComponent<NekoNpcSync>();
        if (authoritySync == null) throw new InvalidOperationException("YUI root is missing replicated authority/session state.");
        // NekoNpcSync on the NPC root is Continuous. Keep this Manual sync
        // behaviour on a distinct GameObject; never change the root's sync mode.
        if (root.GetComponent<NekoNpcSpeechInput>() != null)
            throw new InvalidOperationException("A legacy SpeechInput is on the Continuous NPC root. Remove that SpeechInput before configuring its separate controller.");
        var controller = root.Find("YuiSpeechInputController");
        if (controller == null)
        {
            var controllerObject = new GameObject("YuiSpeechInputController");
            Undo.RegisterCreatedObjectUndo(controllerObject, "Configure YUI microphone input controller");
            controllerObject.transform.SetParent(root, false);
            controller = controllerObject.transform;
        }
        var speech = controller.GetComponent<NekoNpcSpeechInput>();
        if (speech == null) speech = UdonSharpUndo.AddComponent<NekoNpcSpeechInput>(controller.gameObject);
        var backing = UdonSharpEditorUtility.GetBackingUdonBehaviour(speech);
        if (backing == null || backing.programSource == null)
        {
            typeof(UdonSharpEditorUtility).GetMethod("RunBehaviourSetupWithUndo", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { speech });
            backing = UdonSharpEditorUtility.GetBackingUdonBehaviour(speech);
        }
        // Configure immediately; editor validation may otherwise leave the
        // freshly created backing behaviour at its Continuous default.
        backing.SyncMethod = Networking.SyncType.Manual;
        EditorUtility.SetDirty(backing);
        speech.telemetry = router.telemetry; speech.router = router; speech.perception = router.perception; speech.npcRoot = root;
        speech.authoritySync = authoritySync; speech.voice = root.GetComponentInChildren<NekoNpcVoice>(true);
        speech.interactionRange = 4f; speech.leaseSeconds = 12f; speech.nextStartDelaySeconds = 5f;
        speech.sceneAudioSources = Resources.FindObjectsOfTypeAll<AudioSource>()
            .Where(audio => audio.gameObject.scene == scene && !EditorUtility.IsPersistent(audio))
            .OrderBy(audio => GlobalObjectId.GetGlobalObjectIdSlow(audio).ToString()).ToArray();

        var child = root.Find("YuiSpeechInputPanel");
        var go = child == null ? new GameObject("YuiSpeechInputPanel", typeof(RectTransform)) : child.gameObject;
        if (child == null) Undo.RegisterCreatedObjectUndo(go, "Configure YUI microphone input");
        go.transform.SetParent(root, false);
        // Keep the panel in front of YUI and nearby walls instead of embedding
        // its left edge in the spawn area's wall geometry.
        go.transform.localPosition = new Vector3(0f, 1.6f, 1f);
        go.transform.localRotation = Quaternion.Euler(0f, 180f, 0f);
        go.transform.localScale = Vector3.one * .002f;
        go.layer = 0;
        var canvas = go.GetComponent<Canvas>(); if (canvas == null) canvas = go.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace; canvas.sortingOrder = 101;
        var scaler = go.GetComponent<CanvasScaler>(); if (scaler == null) scaler = go.AddComponent<CanvasScaler>();
        scaler.dynamicPixelsPerUnit = 10f;
        if (go.GetComponent<GraphicRaycaster>() == null) go.AddComponent<GraphicRaycaster>();
        if (go.GetComponent<VRCUiShape>() == null) go.AddComponent<VRCUiShape>();
        var rect = go.GetComponent<RectTransform>(); rect.sizeDelta = new Vector2(760, 300);
        rect.anchoredPosition3D = new Vector3(0f, 1.6f, 1f);
        PrefabUtility.RecordPrefabInstancePropertyModifications(rect);
        var body = Rect(go.transform, "SpeechInputBody", Vector2.zero, rect.sizeDelta);
        var image = body.GetComponent<Image>(); if (image == null) image = body.gameObject.AddComponent<Image>();
        image.color = new Color(.035f, .07f, .12f, .94f);
        body.gameObject.SetActive(true); speech.settingsBody = body.gameObject;
        Label(body, "Title", "和 YUI 说话", new Vector2(-90, 112), new Vector2(500, 44), font, 27);
        speech.statusText = Label(body, "Status", "等待 YUI 后端连接。", new Vector2(0, 63), new Vector2(720, 38), font, 21);
        Label(body, "Instructions", "一次仅一位玩家。先解除 VRChat 静音并关闭模型音效。\n说完请点结束并发送；12 秒超时会取消本轮。",
            new Vector2(0, 9), new Vector2(720, 58), font, 20);
        speech.startButton = Button(body, "Start", "开始说话", new Vector2(-245, -68), new Vector2(225, 48), "_StartSpeaking", font, backing);
        speech.finishButton = Button(body, "Finish", "结束并发送", new Vector2(0, -68), new Vector2(225, 48), "_FinishSpeaking", font, backing);
        speech.cancelButton = Button(body, "Cancel", "取消", new Vector2(245, -68), new Vector2(225, 48), "_CancelSpeaking", font, backing);
        Label(body, "Hint", "结束后由后台识别回复。收起会取消当前发言。", new Vector2(0, -120), new Vector2(720, 30), font, 18);
        var toggle = Button(go.transform, "SettingsToggle", "收起", new Vector2(286, 112), new Vector2(164, 44), "_ToggleSettings", font, backing);
        toggle.transform.SetAsLastSibling();
        speech.settingsToggleText = toggle.GetComponentInChildren<TextMeshProUGUI>();
        UdonSharpEditorUtility.CopyProxyToUdon(speech);
        EditorUtility.SetDirty(speech); EditorSceneManager.MarkSceneDirty(scene);
        Debug.Log("[NEKO] Remote microphone-input panel configured. Scene is dirty and was not saved; world Blueprint ID was not changed.");
    }

    static RectTransform Rect(Transform parent, string name, Vector2 position, Vector2 size)
    {
        var found = parent.Find(name);
        var go = found == null ? new GameObject(name, typeof(RectTransform)) : found.gameObject;
        go.transform.SetParent(parent, false); go.layer = 0;
        var rect = go.GetComponent<RectTransform>();
        rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(.5f, .5f);
        rect.anchoredPosition = position; rect.sizeDelta = size;
        return rect;
    }

    static TextMeshProUGUI Label(Transform parent, string name, string content, Vector2 position, Vector2 size, TMP_FontAsset font, int points)
    {
        var rect = Rect(parent, name, position, size);
        var text = rect.GetComponent<TextMeshProUGUI>(); if (text == null) text = rect.gameObject.AddComponent<TextMeshProUGUI>();
        text.font = font; text.text = content; text.fontSize = points;
        text.alignment = TextAlignmentOptions.Center; text.color = Color.white;
        text.enableWordWrapping = true; text.raycastTarget = false;
        return text;
    }

    static Button Button(Transform parent, string name, string title, Vector2 position, Vector2 size, string eventName, TMP_FontAsset font, UdonBehaviour backing)
    {
        var rect = Rect(parent, name, position, size);
        var image = rect.GetComponent<Image>(); if (image == null) image = rect.gameObject.AddComponent<Image>();
        image.color = new Color(.08f, .27f, .39f, 1f);
        var button = rect.GetComponent<UnityEngine.UI.Button>(); if (button == null) button = rect.gameObject.AddComponent<UnityEngine.UI.Button>();
        button.targetGraphic = image; button.navigation = new Navigation { mode = Navigation.Mode.None };
        button.onClick = new UnityEngine.UI.Button.ButtonClickedEvent();
        UnityEventTools.AddStringPersistentListener(button.onClick, backing.SendCustomEvent, eventName);
        Label(rect, "Label", title, Vector2.zero, size, font, 21);
        return button;
    }
}
#endif
