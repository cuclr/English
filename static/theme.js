(function () {
    const storageKey = "vocabulary-theme";
    const availableThemes = new Set(["default", "ocean", "violet", "rose"]);

    function readTheme() {
        try {
            const storedTheme = window.localStorage.getItem(storageKey) || "default";
            return availableThemes.has(storedTheme) ? storedTheme : "default";
        } catch (_error) {
            return "default";
        }
    }

    function applyTheme(theme) {
        if (theme === "default") {
            document.documentElement.removeAttribute("data-theme");
        } else {
            document.documentElement.dataset.theme = theme;
        }
    }

    function saveTheme(theme) {
        try {
            window.localStorage.setItem(storageKey, theme);
        } catch (_error) {
            // The selected theme still applies for this page if storage is unavailable.
        }
    }

    function updateThemeButtons(theme) {
        document.querySelectorAll("[data-theme-option]").forEach((button) => {
            const isSelected = button.dataset.themeOption === theme;
            button.classList.toggle("selected", isSelected);
            button.setAttribute("aria-pressed", String(isSelected));
        });
    }

    const initialTheme = readTheme();
    applyTheme(initialTheme);

    document.addEventListener("DOMContentLoaded", () => {
        updateThemeButtons(initialTheme);
        document.querySelectorAll("[data-theme-option]").forEach((button) => {
            button.addEventListener("click", () => {
                const theme = button.dataset.themeOption;
                if (!availableThemes.has(theme)) return;
                applyTheme(theme);
                saveTheme(theme);
                updateThemeButtons(theme);
            });
        });
    });
})();
