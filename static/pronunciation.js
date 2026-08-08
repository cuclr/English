(() => {
    const accentStorageKey = 'vocabulary-pronunciation-accent';
    const autoStorageKey = 'vocabulary-pronunciation-auto';
    const supportedAccents = new Set(['en-US', 'en-GB']);
    const accentLabels = { 'en-US': '美式', 'en-GB': '英式' };
    const synthesis = window.speechSynthesis;
    const isSupported = Boolean(synthesis && window.SpeechSynthesisUtterance);
    const settings = document.querySelector('[data-pronunciation-settings]');
    const status = document.querySelector('[data-pronunciation-status]');
    const autoWord = settings?.dataset.autoPronounceWord?.trim() || '';
    let voices = [];

    const readStorage = (key, fallback) => {
        try {
            return window.localStorage.getItem(key) ?? fallback;
        } catch (_error) {
            return fallback;
        }
    };

    const saveStorage = (key, value) => {
        try {
            window.localStorage.setItem(key, value);
        } catch (_error) {
            // The preference still works for the current page.
        }
    };

    let accent = readStorage(accentStorageKey, 'en-US');
    if (!supportedAccents.has(accent)) accent = 'en-US';
    let autoPlay = readStorage(autoStorageKey, 'false') === 'true';

    const setStatus = (message) => {
        if (status) status.textContent = message;
    };

    const updateControls = () => {
        document.querySelectorAll('[data-pronunciation-accent]').forEach((button) => {
            const isActive = button.dataset.pronunciationAccent === accent;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-pressed', String(isActive));
        });
        document.querySelectorAll('[data-pronunciation-auto]').forEach((button) => {
            button.classList.toggle('is-active', autoPlay);
            button.setAttribute('aria-pressed', String(autoPlay));
            button.textContent = `自动播放：${autoPlay ? '开' : '关'}`;
        });
        document.querySelectorAll('[data-pronounce-word]').forEach((button) => {
            const word = button.dataset.pronounceWord;
            button.setAttribute('aria-label', `播放 ${word} 的${accentLabels[accent]}发音`);
            button.title = `播放${accentLabels[accent]}发音`;
        });
    };

    const refreshVoices = () => {
        voices = isSupported ? synthesis.getVoices() : [];
    };

    const chooseVoice = () => {
        const normalizedAccent = accent.toLowerCase();
        return voices.find((voice) => voice.lang.toLowerCase() === normalizedAccent)
            || voices.find((voice) => voice.lang.toLowerCase().startsWith(normalizedAccent.slice(0, 2)))
            || null;
    };

    const finishSpeaking = (button) => {
        button?.classList.remove('is-speaking');
        button?.removeAttribute('aria-busy');
    };

    const speak = (word, button = null) => {
        const text = word?.trim();
        if (!text) return;
        if (!isSupported) {
            setStatus('当前浏览器不支持语音朗读，请尝试 Edge、Chrome 或 Safari。');
            return;
        }

        synthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = accent;
        utterance.rate = 0.9;
        const selectedVoice = chooseVoice();
        if (selectedVoice) utterance.voice = selectedVoice;
        utterance.onstart = () => {
            setStatus(`${accentLabels[accent]}发音播放中`);
            button?.classList.add('is-speaking');
            button?.setAttribute('aria-busy', 'true');
        };
        utterance.onend = () => {
            setStatus('');
            finishSpeaking(button);
        };
        utterance.onerror = (event) => {
            finishSpeaking(button);
            if (!['canceled', 'interrupted'].includes(event.error)) {
                setStatus('发音播放失败，请点击喇叭重试。');
            }
        };
        synthesis.speak(utterance);
    };

    document.querySelectorAll('[data-pronounce-word]').forEach((button) => {
        button.addEventListener('click', () => speak(button.dataset.pronounceWord, button));
    });

    document.querySelectorAll('[data-pronunciation-accent]').forEach((button) => {
        button.addEventListener('click', () => {
            accent = button.dataset.pronunciationAccent;
            saveStorage(accentStorageKey, accent);
            updateControls();
            if (autoWord) speak(autoWord);
        });
    });

    document.querySelectorAll('[data-pronunciation-auto]').forEach((button) => {
        button.addEventListener('click', () => {
            autoPlay = !autoPlay;
            saveStorage(autoStorageKey, String(autoPlay));
            updateControls();
            if (autoPlay && autoWord) speak(autoWord);
        });
    });

    if (!isSupported) {
        document.querySelectorAll('[data-pronounce-word], [data-pronunciation-accent], [data-pronunciation-auto]')
            .forEach((button) => { button.disabled = true; });
        setStatus('当前浏览器不支持语音朗读，请尝试 Edge、Chrome 或 Safari。');
    } else {
        refreshVoices();
        synthesis.addEventListener?.('voiceschanged', refreshVoices);
        if (autoPlay && autoWord) {
            window.setTimeout(() => speak(autoWord), 180);
        }
    }

    updateControls();
})();
