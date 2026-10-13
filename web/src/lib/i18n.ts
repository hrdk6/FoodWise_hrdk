import { useCallback, useEffect, useState } from "react";

/** English and Hindi for the two surfaces used away from a desk: the student QR page and kitchen mode. */
const STRINGS = {
  en: {
    howWas: "How was it?",
    liked: "Liked it",
    okay: "Okay",
    disliked: "Didn't like it",
    whatWasOff: "What was off?",
    taste: "Taste",
    quantity: "Quantity",
    temperature: "Temperature",
    freshness: "Freshness",
    variety: "Variety",
    hygiene: "Cleanliness",
    portion: "Portion size",
    tooMuch: "Too much",
    right: "Right",
    tooLittle: "Too little",
    comment: "Anything else? (optional)",
    commentHint: "Any language is fine. Don't include names or phone numbers.",
    send: "Send",
    sent: "Sent",
    thanks: "Thanks. The mess team reads these every week.",
    tomorrow: "Tomorrow",
    eating: "Eating",
    skipping: "Skipping",
    rsvpTitle: "Eating at the mess tomorrow?",
    rsvpHint: "Telling us early helps the kitchen cook the right amount.",
    rsvpClosed: "RSVPs for tomorrow have closed.",
    vote: "Vote for next week's special",
    voted: "Your vote",
    expired: "This QR code has expired. Scan the code on today's table.",
    anonymous: "Anonymous. No sign-in, no personal details.",
    // kitchen
    kitchen: "Kitchen log",
    cooked: "Cooked",
    leftover: "Left over",
    plateWaste: "Plate waste",
    ranOut: "Ran out",
    plan: "Plan",
    save: "Save",
    saved: "Saved",
    savedOffline: "Saved on this device. It will sync when you're back online.",
    offline: "Offline. Entries are kept on this device.",
    pending: "waiting to sync",
    reportSurplus: "Report surplus food",
    serving: "Serving now",
    upcoming: "Later",
    done: "Done",
    diners: "Diners",
  },
  hi: {
    howWas: "कैसा लगा?",
    liked: "अच्छा लगा",
    okay: "ठीक था",
    disliked: "पसंद नहीं आया",
    whatWasOff: "क्या कमी थी?",
    taste: "स्वाद",
    quantity: "मात्रा",
    temperature: "गरमाहट",
    freshness: "ताज़गी",
    variety: "विविधता",
    hygiene: "सफ़ाई",
    portion: "मात्रा कितनी थी",
    tooMuch: "बहुत ज़्यादा",
    right: "सही",
    tooLittle: "बहुत कम",
    comment: "कुछ और कहना है? (वैकल्पिक)",
    commentHint: "किसी भी भाषा में लिखें। नाम या फ़ोन नंबर न लिखें।",
    send: "भेजें",
    sent: "भेज दिया",
    thanks: "धन्यवाद। मेस टीम हर हफ़्ते इन्हें पढ़ती है।",
    tomorrow: "कल",
    eating: "खाऊँगा/खाऊँगी",
    skipping: "नहीं खाऊँगा/खाऊँगी",
    rsvpTitle: "कल मेस में खाना खाएँगे?",
    rsvpHint: "पहले बताने से रसोई सही मात्रा में खाना बनाती है।",
    rsvpClosed: "कल के लिए RSVP बंद हो गया है।",
    vote: "अगले हफ़्ते की स्पेशल डिश चुनें",
    voted: "आपका वोट",
    expired: "यह QR कोड पुराना है। आज की टेबल पर लगा कोड स्कैन करें।",
    anonymous: "गुमनाम। न साइन-इन, न निजी जानकारी।",
    kitchen: "रसोई लॉग",
    cooked: "बनाया",
    leftover: "बचा",
    plateWaste: "थाली में छोड़ा",
    ranOut: "ख़त्म हो गया",
    plan: "योजना",
    save: "सहेजें",
    saved: "सहेज लिया",
    savedOffline: "इस फ़ोन में सहेजा गया। नेटवर्क आने पर भेज दिया जाएगा।",
    offline: "नेटवर्क नहीं है। एंट्री इसी फ़ोन में रखी जा रही हैं।",
    pending: "भेजना बाकी",
    reportSurplus: "बचा हुआ खाना दर्ज करें",
    serving: "अभी परोस रहे हैं",
    upcoming: "बाद में",
    done: "हो गया",
    diners: "खाने वाले",
  },
} as const;

export type Lang = keyof typeof STRINGS;
export type StringKey = keyof (typeof STRINGS)["en"];

const KEY = "fw-lang";

export function useLang(): [Lang, (l: Lang) => void, (k: StringKey) => string] {
  const [lang, setLangState] = useState<Lang>(() => {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved === "hi" || saved === "en") return saved;
    } catch {
      /* storage unavailable */
    }
    return navigator.language?.startsWith("hi") ? "hi" : "en";
  });
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(KEY, l);
    } catch {
      /* ignore */
    }
  }, []);
  const t = useCallback((k: StringKey) => STRINGS[lang][k] ?? STRINGS.en[k], [lang]);
  return [lang, setLang, t];
}
