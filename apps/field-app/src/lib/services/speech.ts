/**
 * Voice-to-text via expo-speech-recognition (needs a development build; the native module is absent in Expo Go).
 * When unavailable the UI falls back to the OS keyboard dictation key on the note TextInput.
 */
let mod: any = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  mod = require('expo-speech-recognition');
} catch {
  mod = null;
}

export const speechAvailable = !!mod?.ExpoSpeechRecognitionModule && (() => {
  try {
    return !!mod.ExpoSpeechRecognitionModule.isRecognitionAvailable?.();
  } catch {
    return false;
  }
})();

export const useSpeechEvent: (name: string, cb: (e: any) => void) => void = mod?.useSpeechRecognitionEvent ?? (() => {});

export async function startDictation(): Promise<boolean> {
  if (!mod) return false;
  const p = await mod.ExpoSpeechRecognitionModule.requestPermissionsAsync();
  if (!p.granted) return false;
  mod.ExpoSpeechRecognitionModule.start({ lang: 'en-US', interimResults: true, continuous: false, requiresOnDeviceRecognition: false });
  return true;
}
export const stopDictation = () => mod?.ExpoSpeechRecognitionModule.stop();
