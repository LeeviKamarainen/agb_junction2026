// The Web Speech API's recognition half is not in TypeScript's DOM types yet, and only Chromium
// browsers ship it (prefixed). Chrome sends the audio to Google for transcription.

export type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
};

type RecognitionEvent = {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
};

type RecognitionConstructor = new () => Recognition;

export function recognitionConstructor(): RecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** Splits a recognition event into the settled words and the ones still being guessed. */
export function transcripts(event: RecognitionEvent): {
  final: string;
  interim: string;
} {
  let final = "";
  let interim = "";
  for (let i = 0; i < event.results.length; i++) {
    const result = event.results[i];
    if (result.isFinal) final += result[0].transcript;
    else interim += result[0].transcript;
  }
  return { final: final.trim(), interim: interim.trim() };
}

export function say(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
