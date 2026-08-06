(() => {
    const button = document.querySelector('#back-to-top');
    if (!button) return;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let updateScheduled = false;

    const updateVisibility = () => {
        const isVisible = window.scrollY > 320;
        button.classList.toggle('is-visible', isVisible);
        button.setAttribute('aria-hidden', String(!isVisible));
        button.tabIndex = isVisible ? 0 : -1;
        updateScheduled = false;
    };

    window.addEventListener('scroll', () => {
        if (updateScheduled) return;
        updateScheduled = true;
        window.requestAnimationFrame(updateVisibility);
    }, { passive: true });

    button.addEventListener('click', () => {
        window.scrollTo({
            top: 0,
            behavior: reducedMotion.matches ? 'auto' : 'smooth',
        });
    });

    updateVisibility();
})();
