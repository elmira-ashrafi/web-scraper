(function () {
  function getCsrfToken() {
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function initTabs(root) {
    root.querySelectorAll('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        const target = this.dataset.tab;
        root.querySelectorAll('.tab').forEach(function (item) {
          item.classList.remove('active');
          item.setAttribute('aria-selected', 'false');
        });
        root.querySelectorAll('.tab-panel').forEach(function (panel) {
          panel.hidden = true;
        });
        this.classList.add('active');
        this.setAttribute('aria-selected', 'true');
        const panel = root.querySelector('#tab-' + target);
        if (panel) panel.hidden = false;
      });
    });
  }

  function initThumbs(root) {
    root.querySelectorAll('.thumb-btn').forEach(function (thumb) {
      thumb.addEventListener('click', function () {
        const mainImg = root.querySelector('#main-image');
        if (mainImg && this.dataset.src) {
          mainImg.src = this.dataset.src;
          root.querySelectorAll('.thumb-btn').forEach(function (item) {
            item.classList.remove('active');
          });
          this.classList.add('active');
        }
      });
    });
  }

  function initLightbox(root) {
    const lightbox = document.getElementById('image-lightbox');
    const imagesEl = document.getElementById('product-images');
    if (!lightbox || !imagesEl) return;

    const images = JSON.parse(imagesEl.textContent);
    const lightboxImg = document.getElementById('lightbox-image');
    const currentEl = document.getElementById('lightbox-current');
    const totalEl = document.getElementById('lightbox-total');
    const prevBtn = lightbox.querySelector('.lightbox-prev');
    const nextBtn = lightbox.querySelector('.lightbox-next');
    let currentIndex = 0;
    let isOpen = false;

    if (totalEl) totalEl.textContent = images.length;

    function updateMainPreview(index) {
      const mainImg = root.querySelector('#main-image');
      if (!mainImg) return;
      mainImg.src = images[index];
      root.querySelectorAll('.thumb-btn').forEach(function (thumb, i) {
        thumb.classList.toggle('active', i === index);
      });
    }

    function showImage(index) {
      currentIndex = (index + images.length) % images.length;
      lightboxImg.classList.add('is-changing');
      lightboxImg.src = images[currentIndex];
      currentEl.textContent = currentIndex + 1;
      prevBtn.hidden = images.length <= 1;
      nextBtn.hidden = images.length <= 1;
      updateMainPreview(currentIndex);
      requestAnimationFrame(function () {
        lightboxImg.classList.remove('is-changing');
      });
    }

    function openLightbox(index) {
      if (isOpen) {
        showImage(index);
        return;
      }
      isOpen = true;
      showImage(index);
      lightbox.hidden = false;
      lightbox.setAttribute('aria-hidden', 'false');
      document.body.classList.add('lightbox-open');
      requestAnimationFrame(function () {
        lightbox.classList.add('is-visible');
      });
      lightbox.querySelector('.lightbox-close')?.focus();
    }

    function closeLightbox() {
      if (!isOpen) return;
      isOpen = false;
      lightbox.classList.remove('is-visible');
      lightbox.classList.add('is-closing');
      document.body.classList.remove('lightbox-open');

      const onEnd = function () {
        lightbox.classList.remove('is-closing');
        lightbox.hidden = true;
        lightbox.setAttribute('aria-hidden', 'true');
        lightboxImg.src = '';
        lightbox.removeEventListener('transitionend', onEnd);
      };
      lightbox.addEventListener('transitionend', onEnd);
      setTimeout(onEnd, 350);
    }

    root.querySelectorAll('.lightbox-trigger').forEach(function (trigger) {
      trigger.addEventListener('click', function () {
        let index = parseInt(this.dataset.index, 10) || 0;
        if (this.classList.contains('main-image-wrap')) {
          const activeThumb = root.querySelector('.thumb-btn.active');
          if (activeThumb) index = parseInt(activeThumb.dataset.index, 10) || 0;
        }
        openLightbox(index);
      });
    });

    lightbox.querySelectorAll('[data-lightbox-close]').forEach(function (el) {
      el.addEventListener('click', closeLightbox);
    });

    prevBtn.addEventListener('click', function (event) {
      event.stopPropagation();
      showImage(currentIndex - 1);
    });

    nextBtn.addEventListener('click', function (event) {
      event.stopPropagation();
      showImage(currentIndex + 1);
    });

    document.addEventListener('keydown', function (event) {
      if (!isOpen) return;
      if (event.key === 'Escape') closeLightbox();
      if (event.key === 'ArrowLeft') showImage(currentIndex - 1);
      if (event.key === 'ArrowRight') showImage(currentIndex + 1);
    });
  }

  function initVariationShowMore(root) {
    root.querySelectorAll('.variation-show-more').forEach(function (button) {
      button.addEventListener('click', function () {
        const dimensionId = this.dataset.dimension;
        const dimension = root.querySelector('[data-dimension-id="' + dimensionId + '"]');
        if (!dimension) return;
        dimension.classList.add('is-expanded');
        this.hidden = true;
      });
    });
  }

  function setVariantLoading(root, isLoading) {
    const section = root.querySelector('.variations-section');
    const indicator = root.querySelector('#variant-loading');
    if (section) section.classList.toggle('is-loading', isLoading);
    if (indicator) indicator.hidden = !isLoading;
    root.querySelectorAll('.variation-option, .variation-dropdown').forEach(function (item) {
      item.disabled = isLoading;
    });
  }

  function loadVariant(asin, root) {
    const container = document.getElementById('product-results');
    const variantUrl = container?.dataset.variantUrl || '/scrape/variant/';
    setVariantLoading(root, true);

    const body = new FormData();
    body.append('asin', asin);

    return fetch(variantUrl, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
      body: body,
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.error || 'Could not load this option.');
          return data;
        });
      })
      .then(function (data) {
        const resultsContainer = document.getElementById('product-results');
        if (!resultsContainer) return;
        resultsContainer.classList.remove('is-loading');
        resultsContainer.innerHTML = data.html;
        if (window.lucide) lucide.createIcons();
        window.initProductCard(resultsContainer);
      })
      .catch(function (error) {
        alert(error.message);
        setVariantLoading(root, false);
      });
  }

  function initVariationSwitching(root) {
    root.querySelectorAll('.variation-option').forEach(function (option) {
      option.addEventListener('click', function () {
        const asin = this.dataset.asin;
        if (!asin || this.classList.contains('is-selected') || this.disabled) return;
        loadVariant(asin, root);
      });
    });
  }

  function initVariationDropdowns(root) {
    root.querySelectorAll('.variation-dropdown').forEach(function (select) {
      const selectedOption = select.options[select.selectedIndex];
      select.dataset.currentAsin = selectedOption ? selectedOption.value : '';

      select.addEventListener('change', function () {
        const asin = this.value;
        if (!asin || this.dataset.currentAsin === asin) return;
        loadVariant(asin, root);
      });
    });
  }

  window.initProductCard = function (scope) {
    const root = scope || document;
    const card = root.querySelector ? root.querySelector('#results') || root : document.getElementById('results');
    if (!card) return;

    initTabs(card);
    initThumbs(card);
    initLightbox(card);
    initVariationShowMore(card);
    initVariationSwitching(card);
    initVariationDropdowns(card);
  };

  document.addEventListener('DOMContentLoaded', function () {
    window.initProductCard();
  });
})();
