const VALID_EXTS = new Set(['jpg', 'jpeg', 'png']);
const EXIF_FIELDS = [
  { key: 'camera_make', label: 'Camera Make', type: 'text' },
  { key: 'camera_model', label: 'Camera Model', type: 'text' },
  { key: 'lens_make', label: 'Lens Make', type: 'text' },
  { key: 'lens_model', label: 'Lens Model', type: 'text' },
  { key: 'focal_length', label: 'Focal Length (mm)', type: 'number', step: 'any' },
  { key: 'f_number', label: 'Aperture (f/)', type: 'number', step: 'any' },
  { key: 'exposure_time', label: 'Exposure Time (s)', type: 'number', step: 'any' },
  { key: 'shutter_speed', label: 'Shutter Speed (s)', type: 'number', step: 'any' },
  { key: 'iso', label: 'ISO', type: 'number', step: '1' },
  { key: 'date', label: 'Date (YYYY:MM:DD HH:MM:SS)', type: 'text', fullWidth: true }
];

// State: array of photo objects
let photos = [];
let isPublishing = false;

const dropzone = document.getElementById('dropzone');
const folderInput = document.getElementById('folderInput');
const filesInput = document.getElementById('filesInput');
const toolbar = document.getElementById('toolbar');
const photoGrid = document.getElementById('photoGrid');
const publishBtn = document.getElementById('publishBtn');
const progressInfo = document.getElementById('progressInfo');
const stagingStatus = document.getElementById('stagingStatus');
const lightbox = document.getElementById('lightbox');
const lightboxImg = document.getElementById('lightboxImg');

function formatTimestamp(dateObj) {
  const d = dateObj instanceof Date ? dateObj : new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}:${pad(d.getMonth() + 1)}:${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function nowFormatted() {
  return formatTimestamp(new Date());
}

function generateId(title) {
  let cleaned = (title || '').replace(/['’]/g, '');
  cleaned = cleaned.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return cleaned || '(auto unique ID)';
}

function isValidFile(name) {
  const parts = (name || '').split('.');
  if (parts.length < 2) return false;
  return VALID_EXTS.has(parts[parts.length - 1].toLowerCase());
}

// Recursively read dropped directory entries
async function readEntryFiles(entry) {
  if (entry.isFile) {
    return new Promise((resolve) => {
      entry.file((f) => resolve(isValidFile(f.name) ? [f] : []), () => resolve([]));
    });
  }
  if (entry.isDirectory) {
    const reader = entry.createReader();
    const entries = [];
    const readBatch = () =>
      new Promise((resolve) => {
        reader.readEntries(async (batch) => {
          if (!batch.length) {
            resolve();
          } else {
            entries.push(...batch);
            await readBatch();
            resolve();
          }
        }, () => resolve());
      });
    await readBatch();
    const nested = await Promise.all(entries.map(readEntryFiles));
    return nested.flat();
  }
  return [];
}

dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('dragover');
});

dropzone.addEventListener('dragleave', () => {
  dropzone.classList.remove('dragover');
});

dropzone.addEventListener('drop', async (e) => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (isPublishing) return;

  const items = Array.from(e.dataTransfer.items || []);
  let collected = [];

  const entries = items
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter(Boolean);

  if (entries.length > 0) {
    const nested = await Promise.all(entries.map(readEntryFiles));
    collected = nested.flat();
  } else {
    collected = Array.from(e.dataTransfer.files || []).filter((f) => isValidFile(f.name));
  }

  if (collected.length > 0) {
    await stageFiles(collected);
  }
});

document.getElementById('selectFolderBtn').addEventListener('click', () => folderInput.click());
document.getElementById('selectFilesBtn').addEventListener('click', () => filesInput.click());

folderInput.addEventListener('change', async (e) => {
  const valid = Array.from(e.target.files || []).filter((f) => isValidFile(f.name));
  folderInput.value = '';
  if (valid.length > 0) await stageFiles(valid);
});

filesInput.addEventListener('change', async (e) => {
  const valid = Array.from(e.target.files || []).filter((f) => isValidFile(f.name));
  filesInput.value = '';
  if (valid.length > 0) await stageFiles(valid);
});

async function stageFiles(fileList) {
  const sorted = [...fileList].sort((a, b) => a.name.localeCompare(b.name));
  let done = 0;
  stagingStatus.textContent = `Staging 0 / ${sorted.length} photos...`;

  for (const file of sorted) {
    const formData = new FormData();
    formData.append('file', file, file.name);
    try {
      const resp = await fetch('/api/stage', { method: 'POST', body: formData });
      const data = await resp.json();
      if (resp.ok) {
        const objectUrl = URL.createObjectURL(file);
        photos.push({
          temp_id: data.temp_id,
          filename: data.filename,
          width: data.width,
          height: data.height,
          previewUrl: objectUrl || data.preview_url,
          title: '',
          description: '',
          is_film: false,
          film_type: '',
          film_date: nowFormatted(),
          exif: { ...data.exif },
          initialMissingExif: new Set(
            Object.entries(data.exif || {})
              .filter(([, v]) => v === null || v === undefined || v === '')
              .map(([k]) => k)
          ),
          status: 'staged',
          error: null
        });
        renderGrid();
      }
    } catch (err) {
      console.error('Failed to stage', file.name, err);
    }
    done++;
    stagingStatus.textContent = `Staging ${done} / ${sorted.length} photos...`;
  }
  stagingStatus.textContent = '';
  updateSummary();
}

function updateSummary() {
  const total = photos.length;
  const publishedCount = photos.filter((p) => p.status === 'published').length;
  const pendingCount = photos.filter((p) => p.status !== 'published').length;

  toolbar.classList.toggle('visible', total > 0);

  if (total === 0) {
    progressInfo.textContent = 'No photos staged.';
    publishBtn.disabled = true;
    publishBtn.textContent = 'Publish All';
  } else {
    progressInfo.textContent = `${total} photo(s) in batch · ${publishedCount} published · ${pendingCount} remaining`;
    publishBtn.disabled = isPublishing || pendingCount === 0;
    publishBtn.textContent = isPublishing
      ? 'Publishing...'
      : `Publish ${pendingCount} Photo${pendingCount === 1 ? '' : 's'}`;
  }
}

function renderGrid() {
  photoGrid.innerHTML = '';

  photos.forEach((photo) => {
    const card = document.createElement('div');
    card.className = `photo-card ${photo.status === 'published' ? 'published' : ''} ${photo.status === 'error' ? 'error' : ''}`;
    const locked = isPublishing || photo.status === 'published';

    const statusLabel =
      photo.status === 'published'
        ? 'Published ✓'
        : photo.status === 'uploading'
        ? 'Uploading...'
        : photo.status === 'error'
        ? 'Error'
        : 'Staged';

    let detailsFieldsHtml = '';
    if (photo.is_film) {
      detailsFieldsHtml = `
        <div class="exif-grid">
          <div class="field-row">
            <label>Film Type</label>
            <input type="text" data-field="film_type" placeholder="Will update later" value="${escapeAttr(photo.film_type)}" ${locked ? 'disabled' : ''} />
          </div>
          <div class="field-row">
            <label>Date</label>
            <input type="text" data-field="film_date" value="${escapeAttr(photo.film_date)}" ${locked ? 'disabled' : ''} />
          </div>
        </div>
      `;
    } else {
      const exifInputs = EXIF_FIELDS.map((f) => {
        const val = photo.exif[f.key];
        const isMissing = photo.initialMissingExif.has(f.key) && (val === null || val === undefined || val === '');
        return `
          <div class="field-row ${f.fullWidth ? 'full-width' : ''}">
            <label>
              <span>${f.label}</span>
              ${isMissing ? '<span class="missing-tag">missing</span>' : ''}
            </label>
            <input
              type="${f.type}"
              ${f.step ? `step="${f.step}"` : ''}
              data-exif="${f.key}"
              value="${escapeAttr(val ?? '')}"
              ${locked ? 'disabled' : ''}
            />
          </div>
        `;
      }).join('');
      detailsFieldsHtml = `<div class="exif-grid">${exifInputs}</div>`;
    }

    card.innerHTML = `
      <div class="card-image-wrap">
        <span class="status-badge ${photo.status}">${statusLabel}</span>
        ${
          photo.status !== 'published'
            ? `<button type="button" class="remove-btn" data-action="remove" title="Remove photo" ${isPublishing ? 'disabled' : ''}>&times;</button>`
            : ''
        }
        <img src="${photo.previewUrl}" alt="${escapeAttr(photo.filename)}" data-action="zoom" />
        ${
          photo.status !== 'published'
            ? `
            <div class="card-actions">
              <button type="button" class="action-btn" data-action="rotate-left" title="Rotate Left" ${isPublishing ? 'disabled' : ''}>↶ Rotate Left</button>
              <button type="button" class="action-btn" data-action="rotate-right" title="Rotate Right" ${isPublishing ? 'disabled' : ''}>↷ Rotate Right</button>
            </div>
            `
            : ''
        }
      </div>
      <div class="card-body">
        <div class="card-meta-header">
          <span class="filename" title="${escapeAttr(photo.filename)}">${escapeHtml(photo.filename)}</span>
          <span data-role="dimensions">${photo.width} &times; ${photo.height}</span>
        </div>

        <div class="field-row">
          <label>
            <span>Title</span>
            <span class="id-preview" data-role="id-preview">ID: ${escapeHtml(generateId(photo.title))}</span>
          </label>
          <input type="text" data-field="title" placeholder="Photo title" value="${escapeAttr(photo.title)}" ${locked ? 'disabled' : ''} />
        </div>

        <div class="field-row">
          <label>Description</label>
          <textarea data-field="description" placeholder="Description" ${locked ? 'disabled' : ''}>${escapeHtml(photo.description)}</textarea>
        </div>

        <label class="film-toggle-row">
          <input type="checkbox" data-field="is_film" ${photo.is_film ? 'checked' : ''} ${locked ? 'disabled' : ''} />
          Film photo
        </label>

        ${detailsFieldsHtml}
        ${photo.error ? `<div class="error-msg">${escapeHtml(photo.error)}</div>` : ''}
      </div>
    `;

    // Wire card events
    card.querySelector('[data-action="zoom"]').addEventListener('click', () => {
      lightboxImg.src = photo.previewUrl;
      lightbox.classList.add('open');
    });

    const rotatePhoto = async (direction) => {
      if (isPublishing) return;
      try {
        const resp = await fetch('/api/rotate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ temp_id: photo.temp_id, direction })
        });
        const data = await resp.json();
        if (resp.ok) {
          photo.width = data.width;
          photo.height = data.height;
          photo.previewUrl = data.preview_url;
          renderGrid();
        } else {
          alert(data.error || 'Failed to rotate photo');
        }
      } catch (err) {
        alert('Rotation error: ' + err.message);
      }
    };

    const rotateLeftBtn = card.querySelector('[data-action="rotate-left"]');
    if (rotateLeftBtn) {
      rotateLeftBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        rotatePhoto('left');
      });
    }

    const rotateRightBtn = card.querySelector('[data-action="rotate-right"]');
    if (rotateRightBtn) {
      rotateRightBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        rotatePhoto('right');
      });
    }

    const removeBtn = card.querySelector('[data-action="remove"]');
    if (removeBtn) {
      removeBtn.addEventListener('click', async () => {
        if (isPublishing) return;
        await fetch(`/api/stage/${photo.temp_id}`, { method: 'DELETE' });
        if (photo.previewUrl && photo.previewUrl.startsWith('blob:')) {
          URL.revokeObjectURL(photo.previewUrl);
        }
        photos = photos.filter((p) => p.temp_id !== photo.temp_id);
        renderGrid();
      });
    }

    const titleInput = card.querySelector('[data-field="title"]');
    const idPreview = card.querySelector('[data-role="id-preview"]');
    titleInput.addEventListener('input', (e) => {
      photo.title = e.target.value;
      idPreview.textContent = `ID: ${generateId(photo.title)}`;
    });

    const descInput = card.querySelector('[data-field="description"]');
    descInput.addEventListener('input', (e) => {
      photo.description = e.target.value;
    });

    const filmCheckbox = card.querySelector('[data-field="is_film"]');
    filmCheckbox.addEventListener('change', (e) => {
      photo.is_film = e.target.checked;
      renderGrid();
    });

    const filmTypeInput = card.querySelector('[data-field="film_type"]');
    if (filmTypeInput) {
      filmTypeInput.addEventListener('input', (e) => {
        photo.film_type = e.target.value;
      });
    }

    const filmDateInput = card.querySelector('[data-field="film_date"]');
    if (filmDateInput) {
      filmDateInput.addEventListener('input', (e) => {
        photo.film_date = e.target.value;
      });
    }

    card.querySelectorAll('[data-exif]').forEach((input) => {
      input.addEventListener('input', (e) => {
        const key = e.target.getAttribute('data-exif');
        photo.exif[key] = e.target.value;
      });
    });

    photoGrid.appendChild(card);
  });

  updateSummary();
}

// Batch actions
document.getElementById('batchDigitalBtn').addEventListener('click', () => {
  if (isPublishing) return;
  photos.forEach((p) => {
    if (p.status !== 'published') p.is_film = false;
  });
  renderGrid();
});

document.getElementById('batchFilmBtn').addEventListener('click', () => {
  if (isPublishing) return;
  const now = Date.now();
  let filmIndex = 0;
  photos.forEach((p) => {
    if (p.status !== 'published') {
      p.is_film = true;
      // Increment each photo by 1 second to create a distinct chronological timeline
      const timestamp = new Date(now + filmIndex * 1000);
      p.film_date = formatTimestamp(timestamp);
      filmIndex++;
    }
  });
  renderGrid();
});

document.getElementById('applyFilmTypeBtn').addEventListener('click', () => {
  if (isPublishing) return;
  const val = document.getElementById('batchFilmType').value.trim();
  photos.forEach((p) => {
    if (p.status !== 'published' && p.is_film) {
      p.film_type = val;
    }
  });
  renderGrid();
});

document.getElementById('clearAllBtn').addEventListener('click', async () => {
  if (isPublishing) return;
  await fetch('/api/clear', { method: 'POST' });
  photos.forEach((p) => {
    if (p.previewUrl && p.previewUrl.startsWith('blob:')) {
      URL.revokeObjectURL(p.previewUrl);
    }
  });
  photos = [];
  renderGrid();
});

// Publish All sequentially
publishBtn.addEventListener('click', async () => {
  if (isPublishing) return;
  const pending = photos.filter((p) => p.status !== 'published');
  if (pending.length === 0) return;

  isPublishing = true;
  renderGrid();

  for (const photo of pending) {
    photo.status = 'uploading';
    photo.error = null;
    renderGrid();

    try {
      const resp = await fetch('/api/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          temp_id: photo.temp_id,
          title: photo.title,
          description: photo.description,
          is_film: photo.is_film,
          film_type: photo.film_type,
          date: photo.film_date,
          exif: photo.exif
        })
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || 'Publish failed');
      }
      photo.status = 'published';
    } catch (err) {
      photo.status = 'error';
      photo.error = err.message || String(err);
    }
    renderGrid();
  }

  isPublishing = false;
  renderGrid();
});

lightbox.addEventListener('click', () => {
  lightbox.classList.remove('open');
  lightboxImg.src = '';
});

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeAttr(str) {
  return escapeHtml(str).replace(/"/g, '&quot;');
}
