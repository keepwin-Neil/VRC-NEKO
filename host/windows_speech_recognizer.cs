// File-only offline Windows speech recognition. Never opens a microphone.
// Microsoft API references:
// https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.speechrecognitionengine.setinputtowavefile
// https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.dictationgrammar
// https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer.setoutputtowavefile
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Speech.AudioFormat;
using System.Speech.Recognition;
using System.Speech.Synthesis;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

internal static class WindowsSpeechRecognizer
{
    private const string SyntheticPhrase = "你好，请你过来。";
    private static readonly JavaScriptSerializer Serializer = new JavaScriptSerializer();

    private static Dictionary<string, object> Result(string culture)
    {
        return new Dictionary<string, object> {
            {"text", ""}, {"confidence", 0.0}, {"engine", null},
            {"culture", culture}, {"error", null}
        };
    }

    private static int Emit(Dictionary<string, object> result, int code)
    {
        Console.WriteLine(Serializer.Serialize(result));
        return code;
    }

    private static bool ValidWave(string path)
    {
        FileInfo info = new FileInfo(path);
        if (!info.Exists || info.Length < 44 || info.Length > 3000000) return false;
        using (BinaryReader reader = new BinaryReader(File.OpenRead(path)))
        {
            if (Encoding.ASCII.GetString(reader.ReadBytes(4)) != "RIFF") return false;
            uint declared = reader.ReadUInt32();
            if ((long)declared + 8 != info.Length || Encoding.ASCII.GetString(reader.ReadBytes(4)) != "WAVE") return false;
            int rate = 0;
            bool formatValid = false;
            long audioBytes = 0;
            while (reader.BaseStream.Position + 8 <= info.Length)
            {
                string chunk = Encoding.ASCII.GetString(reader.ReadBytes(4));
                uint length = reader.ReadUInt32();
                long start = reader.BaseStream.Position;
                long end = start + length;
                if (end > info.Length) return false;
                if (chunk == "fmt ")
                {
                    if (length < 16 || formatValid) return false;
                    int format = reader.ReadUInt16();
                    int channels = reader.ReadUInt16();
                    rate = reader.ReadInt32();
                    int bytesPerSecond = reader.ReadInt32();
                    int alignment = reader.ReadUInt16();
                    int bits = reader.ReadUInt16();
                    formatValid = format == 1 && channels == 1 && (rate == 16000 || rate == 48000)
                        && bytesPerSecond == rate * 2 && alignment == 2 && bits == 16;
                    if (!formatValid) return false;
                }
                else if (chunk == "data") audioBytes += length;
                reader.BaseStream.Position = end + (length % 2);
                if (reader.BaseStream.Position > info.Length) return false;
            }
            return formatValid && audioBytes > 0 && audioBytes % 2 == 0 && audioBytes <= rate * 2L * 30;
        }
    }

    private static int Recognize(string path, string culture, Dictionary<string, object> result)
    {
        if (!ValidWave(path)) { result["error"] = "invalid_wave"; return Emit(result, 4); }
        RecognizerInfo selected = null;
        foreach (RecognizerInfo candidate in SpeechRecognitionEngine.InstalledRecognizers())
            if (String.Equals(candidate.Culture.Name, culture, StringComparison.OrdinalIgnoreCase)) { selected = candidate; break; }
        if (selected == null) { result["error"] = "recognizer_unavailable"; return Emit(result, 3); }
        result["engine"] = selected.Id;
        result["culture"] = selected.Culture.Name;
        using (ManualResetEventSlim finished = new ManualResetEventSlim(false))
        using (SpeechRecognitionEngine engine = new SpeechRecognitionEngine(selected))
        {
            List<string> words = new List<string>();
            double weightedConfidence = 0, durationTotal = 0;
            Exception completionError = null;
            engine.LoadGrammar(new DictationGrammar());
            engine.InitialSilenceTimeout = TimeSpan.FromSeconds(3);
            engine.BabbleTimeout = TimeSpan.FromSeconds(5);
            engine.EndSilenceTimeout = TimeSpan.FromMilliseconds(600);
            engine.EndSilenceTimeoutAmbiguous = TimeSpan.FromMilliseconds(850);
            engine.SpeechRecognized += delegate(object sender, SpeechRecognizedEventArgs args) {
                if (args.Result == null || String.IsNullOrWhiteSpace(args.Result.Text)) return;
                lock (words)
                {
                    words.Add(args.Result.Text.Trim());
                    double duration = Math.Max(0.01, args.Result.Audio.Duration.TotalSeconds);
                    weightedConfidence += args.Result.Confidence * duration;
                    durationTotal += duration;
                }
            };
            engine.RecognizeCompleted += delegate(object sender, RecognizeCompletedEventArgs args) {
                completionError = args.Error;
                finished.Set();
            };
            // Explicit file input; no SetInputToDefaultAudioDevice call anywhere.
            engine.SetInputToWaveFile(path);
            engine.RecognizeAsync(RecognizeMode.Multiple);
            if (!finished.Wait(TimeSpan.FromSeconds(35)))
            {
                engine.RecognizeAsyncCancel();
                finished.Wait(TimeSpan.FromSeconds(2));
                result["error"] = "recognition_timeout";
                return Emit(result, 5);
            }
            if (completionError != null) { result["error"] = "recognition_failed"; return Emit(result, 5); }
            lock (words)
            {
                result["text"] = String.Join(" ", words.ToArray());
                result["confidence"] = durationTotal > 0 ? Math.Round(weightedConfidence / durationTotal, 6) : 0.0;
            }
        }
        return Emit(result, 0);
    }

    private static int Synthesize(string path, string culture, Dictionary<string, object> result)
    {
        // This mode exists only for reproducible synthetic file tests.
        if (File.Exists(path)) { result["error"] = "output_exists"; return Emit(result, 4); }
        using (SpeechSynthesizer synth = new SpeechSynthesizer())
        {
            synth.SelectVoiceByHints(VoiceGender.NotSet, VoiceAge.NotSet, 0, CultureInfo.GetCultureInfo(culture));
            result["engine"] = synth.Voice.Name;
            synth.SetOutputToWaveFile(path, new SpeechAudioFormatInfo(16000, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
            synth.Speak(SyntheticPhrase);
            synth.SetOutputToNull();
        }
        result["text"] = SyntheticPhrase;
        result["confidence"] = 1.0;
        return Emit(result, 0);
    }

    [STAThread]
    private static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        string culture = "zh-CN", mode = null, path = null;
        Dictionary<string, object> result = Result(culture);
        try
        {
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--list" && mode == null) mode = "list";
                else if ((args[i] == "--input" || args[i] == "--synthesize") && mode == null && i + 1 < args.Length)
                { mode = args[i].Substring(2); path = Path.GetFullPath(args[++i]); }
                else if (args[i] == "--culture" && i + 1 < args.Length) culture = CultureInfo.GetCultureInfo(args[++i]).Name;
                else { result["error"] = "invalid_arguments"; return Emit(result, 2); }
            }
            result["culture"] = culture;
            if (mode == "list")
            {
                List<object> engines = new List<object>();
                foreach (RecognizerInfo item in SpeechRecognitionEngine.InstalledRecognizers())
                    engines.Add(new Dictionary<string, object> { {"engine", item.Id}, {"culture", item.Culture.Name} });
                result["engines"] = engines;
                return Emit(result, 0);
            }
            if (mode == "input") return Recognize(path, culture, result);
            if (mode == "synthesize") return Synthesize(path, culture, result);
            result["error"] = "invalid_arguments";
            return Emit(result, 2);
        }
        catch (CultureNotFoundException) { result["error"] = "invalid_culture"; return Emit(result, 2); }
        catch (IOException) { result["error"] = "audio_io_error"; return Emit(result, 4); }
        catch (UnauthorizedAccessException) { result["error"] = "audio_access_denied"; return Emit(result, 4); }
        catch (Exception) { result["error"] = "speech_engine_failed"; return Emit(result, 5); }
    }
}
