(() => {
    const showFavoriteMessage = (message, category = 'success') => {
        let messages = document.querySelector('.messages');
        if (!messages) {
            messages = document.createElement('section');
            messages.className = 'messages';
            messages.setAttribute('aria-live', 'polite');
            document.body.appendChild(messages);
        }

        const notice = document.createElement('p');
        notice.className = `message ${category}`;
        notice.textContent = message;
        messages.appendChild(notice);
        window.setTimeout(() => {
            notice.classList.add('message-leaving');
            notice.addEventListener('animationend', () => notice.remove(), { once: true });
        }, 2600);
    };

    const updateFavoriteButtons = (wordId, isFavorite, word) => {
        document.querySelectorAll(`.favorite-form[data-word-id="${wordId}"]`).forEach((form) => {
            const button = form.querySelector('[data-favorite-button]');
            if (!button) return;
            button.classList.toggle('is-active', isFavorite);
            button.setAttribute('aria-pressed', String(isFavorite));
            button.setAttribute('aria-label', `${isFavorite ? '取消收藏' : '收藏'} ${word}`);
            button.title = isFavorite ? '移出生词簿' : '加入生词簿';
        });
    };

    document.querySelectorAll('.favorite-form').forEach((form) => {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const button = form.querySelector('[data-favorite-button]');
            if (button) button.disabled = true;

            try {
                const response = await fetch(form.action, {
                    method: 'POST',
                    body: new FormData(form),
                    headers: { Accept: 'application/json' },
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || '收藏操作失败。');
                updateFavoriteButtons(result.word_id, result.is_favorite, result.word);
                showFavoriteMessage(result.message);
            } catch (error) {
                showFavoriteMessage(error.message || '收藏操作失败，请稍后重试。', 'error');
            } finally {
                if (button) button.disabled = false;
            }
        });
    });
})();
