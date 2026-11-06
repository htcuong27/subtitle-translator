/**
 * Video Translate Super - OCR Region Selector & Translation Manager
 */

(function () {
  'use strict';

  // --- Constants & Presets ---
  const STORAGE_KEYS = {
    LAST_CROP: 'vts_last_crop',
    DEFAULT_CROP: 'vts_default_crop',
    VIDEO_CROP_PREFIX: 'vts_crop_',
    SOURCE_FOLDER: 'vts_source_folder',
  };

  const PRESETS = {
    bottom_standard: { ymin: 0.78, ymax: 0.96, xmin: 0.12, xmax: 0.88 },
    bottom_low: { ymin: 0.84, ymax: 0.97, xmin: 0.05, xmax: 0.95 },
    middle: { ymin: 0.45, ymax: 0.55, xmin: 0.10, xmax: 0.90 },
    top: { ymin: 0.05, ymax: 0.18, xmin: 0.10, xmax: 0.90 },
    full_width: { ymin: 0.75, ymax: 0.96, xmin: 0.02, xmax: 0.98 },
  };

  let fallbackCrop = PRESETS.bottom_standard;

  // --- State ---
  const state = {
    currentVideo: '',
    folder: '',
    useSeriesOutput: true,
    regions: [],
    activeRegion: 0,
    videos: [],
    selected: new Set(),
    duration: 0,
    videoWidth: 0,
    videoHeight: 0,
    // Crop coordinates normalized (0.0 to 1.0)
    crop: {
      ymin: 0.78,
      ymax: 0.96,
      xmin: 0.12,
      xmax: 0.88,
    },
    isDragging: false,
    dragAction: null, // 'move', 'nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w', 'create'
    dragStart: { x: 0, y: 0 },
    initialCrop: null,
    isTranslating: false,
    eventSource: null,
  };

  // --- DOM Elements ---
  const videoSelect = document.getElementById('videoSelect');
  const btnRefreshVideos = document.getElementById('btnRefreshVideos');

  const videoElement = document.getElementById('videoElement');
  const videoPreview = document.getElementById('videoPreview');
  const cropCanvas = document.getElementById('cropCanvas');
  const ctx = cropCanvas.getContext('2d');
  const cropInfoTag = document.getElementById('cropInfoTag');

  const timelineSlider = document.getElementById('timelineSlider');
  const timeDisplay = document.getElementById('timeDisplay');
  const btnPlayPause = document.getElementById('btnPlayPause');
  const playIcon = document.getElementById('playIcon');
  const btnStepBack = document.getElementById('btnStepBack');
  const btnStepForward = document.getElementById('btnStepForward');
  const btnStepBackLarge = document.getElementById('btnStepBackLarge');
  const btnStepForwardLarge = document.getElementById('btnStepForwardLarge');

  const inputYmin = document.getElementById('inputYmin');
  const inputYmax = document.getElementById('inputYmax');
  const inputXmin = document.getElementById('inputXmin');
  const inputXmax = document.getElementById('inputXmax');
  const cliParamText = document.getElementById('cliParamText');
  const btnResetCrop = document.getElementById('btnResetCrop');
  const btnSaveDefaultCrop = document.getElementById('btnSaveDefaultCrop');
  const savedCropBadge = document.getElementById('savedCropBadge');

  const btnTestOcr = document.getElementById('btnTestOcr');
  const ocrSpinner = document.getElementById('ocrSpinner');
  const ocrCropThumb = document.getElementById('ocrCropThumb');
  const ocrEmptyPlaceholder = document.getElementById('ocrEmptyPlaceholder');
  const ocrResultText = document.getElementById('ocrResultText');

  const outputFolder = document.getElementById('outputFolder');
  const btnDefaultOutput = document.getElementById('btnDefaultOutput');
  const chipSeriesFolder = document.getElementById('chipSeriesFolder');
  const sampleSeriesText = document.getElementById('sampleSeriesText');
  const previewSrtText = document.getElementById('previewSrtText');
  const previewVideoPath = document.getElementById('previewVideoPath');
  const previewVideoText = document.getElementById('previewVideoText');

  const btnStartTranslate = document.getElementById('btnStartTranslate');
  const btnStopTranslate = document.getElementById('btnStopTranslate');
  const btnResetProcess = document.getElementById('btnResetProcess');
  const statusDot = document.getElementById('statusDot');
  const statusText = document.getElementById('statusText');
  const recognitionMode = document.getElementById('recognitionMode');

  const terminalLogs = document.getElementById('terminalLogs');
  const btnClearLog = document.getElementById('btnClearLog');
  const btnCopyLog = document.getElementById('btnCopyLog');

  // --- Initialization ---
  function init() {
    loadLocalSettings();
    state.folder = localStorage.getItem(STORAGE_KEYS.SOURCE_FOLDER) || '';
    bindEvents();
    loadVideoList();
    updateCoordsInputs();
    setupCanvas();
    paintSubtitles();
    const narrowEditor = window.matchMedia('(max-width: 1100px)');
    const placeEditor = () => {
      const panel = document.getElementById('subtitleEditor');
      if (narrowEditor.matches) document.querySelector('.timeline-bar').after(panel);
      else document.querySelector('.panel-side').prepend(panel);
    };
    narrowEditor.addEventListener('change', placeEditor); placeEditor();
    checkStatus().then(() => { if (state.isTranslating) connectLogStream(); });
    updateOutputPreview();
  }

  // --- Local Storage Management ---
  function loadLocalSettings() {
    const savedCrop = localStorage.getItem(STORAGE_KEYS.LAST_CROP) || localStorage.getItem(STORAGE_KEYS.DEFAULT_CROP);
    if (savedCrop) {
      try {
        const parsed = JSON.parse(savedCrop);
        if (isValidCrop(parsed)) {
          fallbackCrop = parsed;
          state.crop = { ...parsed };
        }
      } catch (e) {}
    }
  }

  function isValidCrop(c) {
    return c && typeof c.ymin === 'number' && typeof c.ymax === 'number' &&
      typeof c.xmin === 'number' && typeof c.xmax === 'number' &&
      [c.ymin,c.ymax,c.xmin,c.xmax].every(v => Number.isFinite(v) && v >= 0 && v <= 1) &&
      c.ymin < c.ymax && c.xmin < c.xmax;
  }

  function saveCropToLocal(videoSpecific = false) {
    if (!isValidCrop(state.crop)) return;
    state.regions[state.activeRegion] = {...state.crop};
    try {
      if (videoSpecific && state.currentVideo) {
        localStorage.setItem('vts_regions_' + state.currentVideo, JSON.stringify({regions: state.regions}));
      }
      localStorage.setItem(STORAGE_KEYS.LAST_CROP, JSON.stringify(state.regions[0]));
      if (videoSpecific && state.currentVideo) {
        localStorage.setItem(STORAGE_KEYS.VIDEO_CROP_PREFIX + state.currentVideo, JSON.stringify(state.regions[0]));
      }
    } catch (e) {}
  }

  function savedRegions(video) {
    try {
      const saved = JSON.parse(localStorage.getItem('vts_regions_' + video));
      if (saved?.regions?.length && saved.regions.every(isValidCrop)) return saved.regions;
      const legacy = JSON.parse(localStorage.getItem(STORAGE_KEYS.VIDEO_CROP_PREFIX + video));
      if (isValidCrop(legacy)) return [legacy];
    } catch (e) {}
    return null;
  }

  function applyRegions(regions) {
    state.regions = regions;
    state.activeRegion = 0;
    state.crop = {...regions[0]};
    updateRegionSelect();
    renderCropBox();
    updateCoordsInputs();
  }

  function ocrRegionConfig(regions = state.regions) {
    return {regions: regions.map(box => [box.ymin, box.ymax, box.xmin, box.xmax])};
  }

  function updateRegionSelect() {
    const select = document.getElementById('ocrRegionSelect');
    select.replaceChildren();
    state.regions.forEach((box, index) => {
      const option = document.createElement('option');
      option.value = index;
      option.textContent = `OCR ${index + 1}`;
      select.appendChild(option);
    });
    select.value = state.activeRegion;
  }

  const pendingDetection = new Map();

  function detectRegion(video, folder) {
    if (!pendingDetection.has(video)) {
      const request = editorRequest('/api/detect_ocr', {video, folder}).then(({region}) => {
        if (!region) return null;
        const [ymin, ymax, xmin, xmax] = region;
        const crop = {ymin, ymax, xmin, xmax};
        if (!isValidCrop(crop)) return null;
        if (!savedRegions(video)) localStorage.setItem('vts_regions_' + video, JSON.stringify({regions: [crop]}));
        return savedRegions(video);
      }).finally(() => pendingDetection.delete(video));
      pendingDetection.set(video, request);
    }
    return pendingDetection.get(video);
  }

  function showSavedBadge(msg = 'Đã ghi nhớ toạ độ') {
    if (!savedCropBadge) return;
    savedCropBadge.textContent = msg;
    savedCropBadge.style.display = 'inline-block';
    clearTimeout(savedCropBadge._timer);
    savedCropBadge._timer = setTimeout(() => {
      savedCropBadge.style.display = 'none';
    }, 2500);
  }

  // --- API Calls ---
  let listRequest = 0;
  let scanning = false;
  let preparing = false;
  async function loadVideoList() {
    const request = ++listRequest;
    const folder = state.folder;
    const output = outputFolder.value.trim() || 'outputs';
    const folderLabel = document.getElementById('sourceFolder');
    scanning = true;
    updateStartButton();
    try {
      const res = await fetch('/api/videos?' + new URLSearchParams({folder, output}));
      const data = await res.json();
      if (request !== listRequest) return;
      if (!res.ok) throw new Error(data.error || 'Không thể quét thư mục');
      state.folder = data.folder;
      state.videos = data.videos;
    } catch (err) {
      if (request !== listRequest) return;
      state.videos = [];
      logMessage(`Lỗi tải danh sách video: ${err.message}`, 'error');
    }
    scanning = false;
    folderLabel.textContent = state.folder ? state.folder.split('/').pop() || '/' : 'Chưa chọn thư mục';
    folderLabel.title = state.folder;
    state.selected = new Set([...state.selected].filter(path => canSelect(path)));
    renderVideoQueue();
    const first = state.videos.find(canQueue) || state.videos[0];
    videoSelect.replaceChildren(new Option(first ? 'Chọn video' : state.folder ? 'Không tìm thấy video' : 'Chọn thư mục video', ''));
    for (const video of state.videos) {
      const label = video.subtitled ? ' · Đã sub' : '';
      const option = new Option(`${video.rel_path} (${video.size})${label}`, video.path);
      videoSelect.add(option);
    }
    if (!state.videos.some(video => video.path === state.currentVideo) && !editorDirty) loadVideo(first?.path || '');
    videoSelect.value = state.videos.some(video => video.path === state.currentVideo) ? state.currentVideo : '';
    updateStartButton();
  }

  function canQueue(video) {
    return !video.subtitled || document.querySelector('input[name="outputMode"]:checked').value !== 'srt';
  }

  function canSelect(path) {
    return state.videos.some(video => video.path === path && canQueue(video));
  }

  function updateStartButton() {
    btnStartTranslate.disabled = scanning || preparing || !(state.selected.size || canSelect(state.currentVideo));
  }

  function renderVideoQueue() {
    const container = document.getElementById('videoQueue');
    container.replaceChildren();
    for (const video of state.videos) {
      const row = document.createElement('label');
      row.className = 'video-queue-row';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.disabled = !canQueue(video);
      checkbox.checked = state.selected.has(video.path);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) state.selected.add(video.path);
        else state.selected.delete(video.path);
        document.getElementById('selectionCount').textContent = `Đã chọn ${state.selected.size} video`;
        updateOutputPreview();
        updateStartButton();
      });
      const name = document.createElement('span');
      name.textContent = video.rel_path + (video.subtitled ? ' · Đã sub' : '');
      const preview = document.createElement('button');
      preview.type = 'button';
      preview.className = 'btn btn-sm btn-secondary';
      preview.textContent = 'Xem';
      preview.addEventListener('click', (event) => { event.preventDefault(); videoSelect.value = video.path; loadVideo(video.path); });
      row.append(checkbox, name, preview);
      container.append(row);
    }
    document.getElementById('selectionCount').textContent = `Đã chọn ${state.selected.size} video · Không chọn sẽ chạy video đang xem`;
    updateOutputPreview();
    updateStartButton();
  }

  function renderProgress(items = []) {
    const container = document.getElementById('queueProgress');
    container.replaceChildren();
    const labels = {pending: 'Chờ', running: 'Đang xử lý', completed: 'Hoàn tất', failed: 'Lỗi', stopped: 'Đã dừng'};
    items.forEach((item, index) => {
      const row = document.createElement('div');
      row.className = `queue-result queue-${item.status}`;
      row.textContent = `${index + 1}. ${item.video.split('/').slice(-2).join('/')} · ${labels[item.status] || item.status}`;
      if (item.progress) {
        const p = item.progress;
        const stages = {processing:'Đang xử lý AI',muxing:'Đang ghép âm thanh',burning_subtitles:'Đang in SRT',completed:'Đã xuất xong',failed:'Xuất lỗi'};
        const detail = document.createElement('div');
        detail.textContent = `${stages[p.stage] || p.stage} · ${p.percent ?? 0}%` +
          (p.eta_seconds ? ` · còn khoảng ${(p.eta_seconds/3600).toFixed(1)} giờ` : '') +
          (p.error ? ` · ${p.error}` : '');
        row.append(detail);
        if (p.output) {
          const path = document.createElement('div'); path.textContent = p.output; row.append(path);
          if (p.stage === 'completed') {
            const link = document.createElement('a'); link.textContent = 'Mở video đã xuất';
            link.href = '/api/stream?path=' + encodeURIComponent(p.output); link.target = '_blank'; row.append(link);
          }
        }
      }
      container.append(row);
    });
  }

  async function loadVideo(path) {
    if ((path && !state.videos.some(video => video.path === path)) || path === state.currentVideo) return;
    if (editorDirty && !confirm('Đổi video sẽ bỏ phần sửa chưa lưu. Tiếp tục?')) {
      videoSelect.value = state.currentVideo;
      return;
    }
    resetDirectEditor();
    editOnVideo.checked = false;
    state.currentVideo = path;
    const status = document.getElementById('ocrDetectionStatus');
    status.textContent = '';
    if (!path) {
      state.duration = 0;
      timelineSlider.value = 0;
      updateTimeDisplay(0);
      videoElement.removeAttribute('src');
      videoElement.load();
      ctx.clearRect(0, 0, cropCanvas.width, cropCanvas.height);
      cropInfoTag.style.display = 'none';
      updateOutputPreview();
      updateStartButton();
      return;
    }
    const saved = savedRegions(path);
    applyRegions(saved || [{...fallbackCrop}]);
    updateOutputPreview();
    updateStartButton();
    logMessage(`Đang tải video: ${path}`, 'system');
    videoElement.src = `/api/stream?path=${encodeURIComponent(path)}`;
    videoElement.load();
    if (saved) {
      status.textContent = 'Đang dùng vùng OCR đã lưu.';
      return;
    }
    status.textContent = 'Đang tự dò vùng OCR…';
    try {
      const detected = await detectRegion(path, state.folder);
      if (state.currentVideo !== path) return;
      if (detected && !state.isDragging) applyRegions(detected);
      status.textContent = detected ? 'Đã dò vùng OCR. Bạn có thể chỉnh lại.' : 'Chưa tìm được vùng chữ rõ. Hãy kiểm tra vùng OCR trên video.';
    } catch (error) {
      if (state.currentVideo === path) status.textContent = `Lỗi dò OCR: ${error.message}`;
    }
  }

  function getSeriesName(videoPath) {
    if (!videoPath) return '{tên-bộ}';
    const parts = videoPath.split('/').filter(Boolean);
    if (parts.length >= 2) {
      // The parent folder of the video file is usually the series name
      return parts[parts.length - 2];
    }
    return 'series-name';
  }

  function updateOutputPreview() {
    if (!outputFolder) return;
    const series = getSeriesName(state.currentVideo);
    const seriesOutput = `outputs/${series}`;
    if (state.useSeriesOutput) outputFolder.value = state.currentVideo ? seriesOutput : '';
    paintSubtitles();
    const val = outputFolder.value.trim() || 'outputs';

    if (chipSeriesFolder) {
      const codeEl = chipSeriesFolder.querySelector('code');
      if (codeEl) codeEl.textContent = `outputs/${series}`;
    }
    if (sampleSeriesText) {
      sampleSeriesText.textContent = `outputs/${series}`;
    }

    // Active state on sample chips
    document.querySelectorAll('.chip-sample').forEach((chip) => {
      const s = chip.dataset.sample;
      chip.classList.toggle('active', state.useSeriesOutput ? s === 'series' : s === val);
    });

    const selected = [...state.selected];
    const sample = selected.length ? selected[0] : state.currentVideo;
    const epName = sample ? sample.split('/').pop().replace(/\.[^/.]+$/, '') : 'ep-001';
    let sampleFolder = val;
    if (selected.length > 1) {
      const parents = selected.map(path => path.split('/').slice(0, -1));
      let common = 0;
      while (common < parents[0].length && parents.every(parts => parts[common] === parents[0][common])) common++;
      const relative = parents[0].slice(common).join('/');
      if (relative) sampleFolder += `/${relative}`;
    }
    if (previewSrtText) previewSrtText.textContent = `${sampleFolder}/${epName}/outputs/${epName}.vi.srt`;

    if (editorDirty) { alert('Hãy lưu SRT đang sửa trước khi chạy hàng đợi.'); return; }
    const selectedMode = document.querySelector('input[name="outputMode"]:checked')?.value || 'srt_burn';
    if (previewVideoPath && previewVideoText) {
      if (selectedMode === 'soft') {
        previewVideoPath.style.display = 'flex';
        previewVideoText.textContent = `${sampleFolder}/${epName}/outputs/${epName}.vi.softsub.mp4`;
      } else if ((selectedMode === 'burn' || selectedMode === 'srt_burn')) {
        previewVideoPath.style.display = 'flex';
        previewVideoText.textContent = `${sampleFolder}/${epName}/outputs/${epName}.vi.burned.mp4`;

      } else {
        previewVideoPath.style.display = 'none';
      }
    }
  }

  // --- Canvas & Crop Box Handling ---
  function setupCanvas() {
    const hideVideo = document.getElementById('hideVideo');
    const updateVideoVisibility = () => { videoPreview.hidden = hideVideo.checked; };
    hideVideo.addEventListener('change', updateVideoVisibility);
    updateVideoVisibility();

    function resizeCanvas() {
      const rect = videoElement.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        cropCanvas.width = rect.width;
        cropCanvas.height = rect.height;
        cropCanvas.style.width = `${rect.width}px`;
        cropCanvas.style.height = `${rect.height}px`;
        renderCropBox();
      }
    }

    videoElement.addEventListener('loadedmetadata', () => {
      state.duration = videoElement.duration || 0;
      state.videoWidth = videoElement.videoWidth;
      state.videoHeight = videoElement.videoHeight;
      document.getElementById('stageWrapper').style.setProperty('--video-ratio', String(state.videoWidth / state.videoHeight || 16 / 9));
      timelineSlider.max = state.duration;
      timelineSlider.value = 0;
      updateTimeDisplay(0);
      resizeCanvas();
    });

    window.addEventListener('resize', resizeCanvas);
    new ResizeObserver(resizeCanvas).observe(videoElement);
    document.getElementById('expandVideo').addEventListener('click', async () => {
      try { await document.getElementById('stageContainer').requestFullscreen(); }
      catch (error) { editorStatus.textContent = 'Không mở được toàn màn hình: ' + error.message; }
    });
    videoElement.addEventListener('play', () => { playIcon.textContent = '⏸'; });
    videoElement.addEventListener('pause', () => { playIcon.textContent = '▶'; });
    videoElement.addEventListener('timeupdate', () => {
      if (!state.isDragging) {
        timelineSlider.value = videoElement.currentTime;
        updateTimeDisplay(videoElement.currentTime);
      }
    });

    // Mouse events on Canvas
    cropCanvas.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  }

  function getPixelBox() {
    const w = cropCanvas.width;
    const h = cropCanvas.height;
    return {
      x: state.crop.xmin * w,
      y: state.crop.ymin * h,
      w: (state.crop.xmax - state.crop.xmin) * w,
      h: (state.crop.ymax - state.crop.ymin) * h,
    };
  }

  function renderCropBox() {
    const w = cropCanvas.width;
    const h = cropCanvas.height;
    if (!state.currentVideo || w === 0 || h === 0) return;

    ctx.clearRect(0, 0, w, h);

    const b = getPixelBox();
    state.regions.forEach((box, index) => {
      if (index === state.activeRegion) return;
      ctx.strokeStyle = '#06b6d4';
      ctx.lineWidth = 2;
      ctx.strokeRect(box.xmin*w, box.ymin*h, (box.xmax-box.xmin)*w, (box.ymax-box.ymin)*h);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fillText(`OCR ${index + 1}`, box.xmin*w+4, box.ymin*h+14);
    });

    // 2. Crop Box Border & Glow
    ctx.save();
    ctx.strokeStyle = '#06b6d4';
    ctx.lineWidth = 2;
    ctx.shadowColor = 'rgba(6, 182, 212, 0.6)';
    ctx.shadowBlur = 8;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    ctx.restore();

    // 3. Subtitle Center Reference Line (dashed)
    ctx.save();
    ctx.strokeStyle = 'rgba(6, 182, 212, 0.35)';
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(b.x, b.y + b.h / 2);
    ctx.lineTo(b.x + b.w, b.y + b.h / 2);
    ctx.stroke();
    ctx.restore();

    // 4. Resize Handles (4 corners + 4 edges)
    const handles = getHandles(b);
    handles.forEach((pt) => {
      ctx.fillStyle = '#06b6d4';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    });

    // 5. Update Floating Tag Position
    cropInfoTag.style.display = 'block';
    const tagX = Math.max(10, Math.min(w - 220, b.x));
    const tagY = b.y > 30 ? b.y - 28 : b.y + b.h + 8;
    cropInfoTag.style.transform = `translate(${tagX}px, ${tagY}px)`;
    cropInfoTag.textContent = `Y: ${(state.crop.ymin * 100).toFixed(1)}% - ${(state.crop.ymax * 100).toFixed(1)}% | X: ${(state.crop.xmin * 100).toFixed(1)}% - ${(state.crop.xmax * 100).toFixed(1)}%`;
  }

  function getHandles(b) {
    return [
      { id: 'nw', x: b.x, y: b.y },
      { id: 'ne', x: b.x + b.w, y: b.y },
      { id: 'sw', x: b.x, y: b.y + b.h },
      { id: 'se', x: b.x + b.w, y: b.y + b.h },
      { id: 'n', x: b.x + b.w / 2, y: b.y },
      { id: 's', x: b.x + b.w / 2, y: b.y + b.h },
      { id: 'w', x: b.x, y: b.y + b.h / 2 },
      { id: 'e', x: b.x + b.w, y: b.y + b.h / 2 },
    ];
  }

  function detectHit(mx, my) {
    const b = getPixelBox();
    const handles = getHandles(b);
    for (const h of handles) {
      const dist = Math.hypot(mx - h.x, my - h.y);
      if (dist <= 8) return h.id;
    }
    if (mx >= b.x && mx <= b.x + b.w && my >= b.y && my <= b.y + b.h) {
      return 'move';
    }
    return null;
  }

  function onMouseDown(e) {
    if (!state.currentVideo) return;
    const rect = cropCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const hit = detectHit(mx, my);
    state.isDragging = true;
    state.dragStart = { x: mx, y: my };
    state.initialCrop = { ...state.crop };

    if (hit) {
      state.dragAction = hit;
    } else {
      state.dragAction = 'create';
      const nx = Math.max(0, Math.min(1, mx / cropCanvas.width));
      const ny = Math.max(0, Math.min(1, my / cropCanvas.height));
      state.crop = { ymin: ny, ymax: ny, xmin: nx, xmax: nx };
    }
  }

  function onMouseMove(e) {
    const rect = cropCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    if (!state.isDragging) {
      const hit = detectHit(mx, my);
      if (hit === 'move') cropCanvas.style.cursor = 'move';
      else if (hit === 'nw' || hit === 'se') cropCanvas.style.cursor = 'nwse-resize';
      else if (hit === 'ne' || hit === 'sw') cropCanvas.style.cursor = 'nesw-resize';
      else if (hit === 'n' || hit === 's') cropCanvas.style.cursor = 'ns-resize';
      else if (hit === 'w' || hit === 'e') cropCanvas.style.cursor = 'ew-resize';
      else cropCanvas.style.cursor = 'crosshair';
      return;
    }

    const dxNorm = (mx - state.dragStart.x) / cropCanvas.width;
    const dyNorm = (my - state.dragStart.y) / cropCanvas.height;
    const c0 = state.initialCrop;

    if (state.dragAction === 'move') {
      const width = c0.xmax - c0.xmin;
      const height = c0.ymax - c0.ymin;
      let newXmin = c0.xmin + dxNorm;
      let newYmin = c0.ymin + dyNorm;

      newXmin = Math.max(0, Math.min(1 - width, newXmin));
      newYmin = Math.max(0, Math.min(1 - height, newYmin));

      state.crop = {
        xmin: newXmin,
        xmax: newXmin + width,
        ymin: newYmin,
        ymax: newYmin + height,
      };
    } else if (state.dragAction === 'create') {
      const curX = Math.max(0, Math.min(1, mx / cropCanvas.width));
      const curY = Math.max(0, Math.min(1, my / cropCanvas.height));
      const startX = state.dragStart.x / cropCanvas.width;
      const startY = state.dragStart.y / cropCanvas.height;

      state.crop = {
        xmin: Math.min(startX, curX),
        xmax: Math.max(startX, curX),
        ymin: Math.min(startY, curY),
        ymax: Math.max(startY, curY),
      };
    } else {
      let { ymin, ymax, xmin, xmax } = c0;
      if (state.dragAction.includes('n')) ymin = Math.min(ymax - 0.02, Math.max(0, c0.ymin + dyNorm));
      if (state.dragAction.includes('s')) ymax = Math.max(ymin + 0.02, Math.min(1, c0.ymax + dyNorm));
      if (state.dragAction.includes('w')) xmin = Math.min(xmax - 0.02, Math.max(0, c0.xmin + dxNorm));
      if (state.dragAction.includes('e')) xmax = Math.max(xmin + 0.02, Math.min(1, c0.xmax + dxNorm));

      state.crop = { ymin, ymax, xmin, xmax };
    }

    renderCropBox();
    updateCoordsInputs();
  }

  function onMouseUp() {
    if (!state.isDragging) return;
    state.isDragging = false;
    if (state.crop.xmax - state.crop.xmin < 0.04 || state.crop.ymax - state.crop.ymin < 0.02) {
      applyPreset('bottom_standard');
    }
    renderCropBox();
    updateCoordsInputs();
    saveCropToLocal(true);
  }

  // --- UI & Controls ---
  function updateCoordsInputs() {
    inputYmin.value = (state.crop.ymin * 100).toFixed(1);
    inputYmax.value = (state.crop.ymax * 100).toFixed(1);
    inputXmin.value = (state.crop.xmin * 100).toFixed(1);
    inputXmax.value = (state.crop.xmax * 100).toFixed(1);

    const param = `${state.regions.length || 1} vùng OCR · Đang chỉnh: OCR ${state.activeRegion + 1}`;
    cliParamText.textContent = param;
  }

  function readCoordsInputs() {
    state.crop.ymin = Math.max(0, Math.min(100, parseFloat(inputYmin.value) || 0)) / 100;
    state.crop.ymax = Math.max(0, Math.min(100, parseFloat(inputYmax.value) || 0)) / 100;
    state.crop.xmin = Math.max(0, Math.min(100, parseFloat(inputXmin.value) || 0)) / 100;
    state.crop.xmax = Math.max(0, Math.min(100, parseFloat(inputXmax.value) || 0)) / 100;
    renderCropBox();
    updateCoordsInputs();
    saveCropToLocal(true);
  }

  function applyPreset(name) {
    if (!PRESETS[name]) return;
    state.crop = { ...PRESETS[name] };
    document.querySelectorAll('.btn-preset').forEach((b) => {
      b.classList.toggle('active', b.dataset.preset === name);
    });
    renderCropBox();
    updateCoordsInputs();
    saveCropToLocal(true);
  }

  function formatTime(seconds) {
    const s = Math.max(0, seconds);
    const mins = Math.floor(s / 60);
    const secs = Math.floor(s % 60);
    const ms = Math.floor((s % 1) * 100);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(ms).padStart(2, '0')}`;
  }

  function updateTimeDisplay(current) {
    timeDisplay.textContent = `${formatTime(current)} / ${formatTime(state.duration)}`;
  }

  let editorContext = null;
  let editorDirty = false;
  const editor = document.getElementById('subtitleText');
  const instant = document.getElementById('instantText');
  const editorStatus = document.getElementById('editorStatus');
  let cues = [];
  let selectedCue = -1;
  let rawValid = true;
  const overlay = document.getElementById('subtitleOverlay');
  const cueList = document.getElementById('cueList');
  const editOnVideo = document.getElementById('editOnVideo');
  function sameEditorContext() {
    return editorContext && JSON.stringify(editorContext) === JSON.stringify(currentEditorContext());
  }
  function resetDirectEditor() {
    editorContext = null; editorDirty = false; cues = []; selectedCue = -1;
    editor.value = ''; instant.value = ''; rawValid = true;
    document.getElementById('subtitleSource').value = '';
    overlay.replaceChildren(); cueList.replaceChildren();
    document.getElementById('selectedCue').textContent = 'Tải phụ đề cho video đang xem để bắt đầu.';
  }
  function requireEditor() {
    if (!sameEditorContext()) throw new Error('Bấm Tải SRT của video đang xem trước khi chỉnh.');
    if (!rawValid) throw new Error('Sửa lỗi SRT nâng cao trước khi chỉnh trên video.');
  }
  function selectCue(index, seek = false) {
    selectedCue = index;
    const cue = cues[index];
    if (!cue) return;
    instant.value = cue.text;
    document.getElementById('textStart').value = cue.start.toFixed(3);
    document.getElementById('textEnd').value = cue.end.toFixed(3);
    document.getElementById('selectedCue').textContent = `Đang sửa câu ${index + 1} · ${Subtitles.timestamp(cue.start)} → ${Subtitles.timestamp(cue.end)}`;
    if (seek) { videoElement.pause(); videoElement.currentTime = cue.start; }
    renderCueList();
    paintSubtitles();
    if (seek && !videoPreview.hidden && window.matchMedia('(max-width: 1100px)').matches) videoElement.scrollIntoView({block: 'center'});
  }
  function renderCueList() {
    const fragment = document.createDocumentFragment();
    cues.forEach((cue, index) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'cue-row';
      button.setAttribute('aria-pressed', String(index === selectedCue));
      button.textContent = `${index + 1} · ${Subtitles.timestamp(cue.start)}  ${cue.text}`;
      button.addEventListener('click', () => selectCue(index, true));
      fragment.append(button);
    });
    cueList.replaceChildren(fragment);
  }
  function commitCues(repaint = true) {
    editor.value = Subtitles.serialize(cues); editorDirty = true; rawValid = true;
    renderCueList();
    if (repaint) paintSubtitles();
  }
  function paintSubtitles() {
    const enabled = editOnVideo.checked;
    cropCanvas.style.pointerEvents = enabled ? 'none' : 'auto';
    cropCanvas.style.visibility = enabled ? 'hidden' : 'visible';
    document.getElementById('cropBoxGuide').hidden = enabled;
    if (overlay.contains(document.activeElement)) return;
    overlay.replaceChildren();
    if (!enabled || !sameEditorContext() || !rawValid) return;
    for (const index of Subtitles.active(cues, videoElement.currentTime || 0)) {
      const line = document.createElement('div');
      line.dataset.cueIndex = String(index);
      line.contentEditable = 'plaintext-only'; line.setAttribute('role', 'textbox');
      line.setAttribute('aria-label', `Phụ đề trên video — câu ${index + 1}`);
      line.setAttribute('aria-multiline', 'true'); line.tabIndex = 0;
      line.textContent = cues[index].text;
      line.addEventListener('focus', () => { videoElement.pause(); selectCue(index); });
      line.addEventListener('input', () => {
        cues[index].text = line.innerText.replace(/\r/g, '');
        instant.value = cues[index].text;
        commitCues(false);
        editorStatus.textContent = 'Đã sửa trên video · chưa lưu.';
      });
      line.addEventListener('blur', () => {
        if (!cues[index].text.trim()) {
          cues[index].text = 'Nhập phụ đề'; commitCues(false);
          editorStatus.textContent = 'Dùng Xóa câu đang chọn nếu muốn bỏ câu này.';
        }
        paintSubtitles();
      });
      overlay.append(line);
    }
  }
  editor.addEventListener('input', () => {
    editorDirty = true;
    try { cues = Subtitles.parse(editor.value); rawValid = true; selectedCue = -1; renderCueList(); }
    catch (error) { rawValid = false; editorStatus.textContent = error.message; }
    paintSubtitles();
  });
  editOnVideo.addEventListener('change', paintSubtitles);
  videoElement.addEventListener('timeupdate', paintSubtitles);
  videoElement.addEventListener('seeked', paintSubtitles);
  outputFolder.addEventListener('input', paintSubtitles);
  document.getElementById('videoQueue').addEventListener('change', paintSubtitles);
  window.addEventListener('beforeunload', event => { if (editorDirty) { event.preventDefault(); event.returnValue = ''; } });
  function currentEditorContext() {
    return {video: state.currentVideo,
      videos: state.selected.has(state.currentVideo) ? [...state.selected] : [state.currentVideo],
      output: outputFolder.value.trim() || 'outputs'};
  }
  async function editorRequest(endpoint, payload) {
    const response = await fetch(endpoint, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || 'Yêu cầu thất bại');
    return data;
  }
  const aiSettings = document.getElementById('aiSettings');
  let settingsScrollY = 0;
  document.getElementById('openAiSettings').addEventListener('click', () => {
    settingsScrollY = window.scrollY;
    document.body.style.setProperty('--settings-scroll-offset', `-${settingsScrollY}px`);
    document.body.classList.add('settings-open');
    aiSettings.showModal();
  });
  aiSettings.addEventListener('close', () => {
    document.body.classList.remove('settings-open');
    document.body.style.removeProperty('--settings-scroll-offset');
    window.scrollTo({top: settingsScrollY, behavior: 'instant'});
  });
  document.getElementById('closeAiSettings').addEventListener('click', () => aiSettings.close());
  const aiProvider = document.getElementById('aiProvider');
  const aiModel = document.getElementById('aiModel');
  const aiBase = document.getElementById('aiBaseUrl');
  const aiKey = document.getElementById('aiKey');
  const aiClear = document.getElementById('aiClearKey');
  const aiBackup = document.getElementById('aiBackupKeys');
  const aiClearBackup = document.getElementById('aiClearBackupKeys');
  const aiSave = document.getElementById('aiSave');
  const aiTest = document.getElementById('aiTest');
  const aiStatus = document.getElementById('aiStatus');
  let aiConfig;
  function showAIProfile() {
    const {model, base_url, has_key, backup_key_count = 0} = aiConfig.profiles[aiProvider.value];
    const compatible = aiProvider.value === 'compatible';
    document.getElementById('aiBaseField').hidden = !compatible;
    aiBase.required = compatible;
    aiBase.value = base_url;
    aiModel.value = model;
    aiKey.value = '';
    aiKey.placeholder = has_key ? 'Đã cấu hình API key' : 'Nhập API key';
    aiClear.checked = false;
    aiClear.disabled = !has_key;
    document.getElementById('aiBackupField').hidden = aiProvider.value !== 'gemini';
    aiBackup.value = '';
    aiBackup.placeholder = backup_key_count ? `Đã lưu ${backup_key_count} key dự phòng` : 'Key 1, key 2, …';
    aiClearBackup.checked = false;
    aiClearBackup.disabled = !backup_key_count;
  }
  aiSettings.addEventListener('toggle', async () => {
    if (!aiSettings.open || aiConfig) return;
    try {
      const response = await fetch('/api/ai_settings', {cache: 'no-store'});
      if (!response.ok) throw new Error('Không đọc được cấu hình AI.');
      aiConfig = await response.json();
      aiProvider.value = aiConfig.provider;
      showAIProfile();
      aiSave.disabled = aiTest.disabled = false;
      aiStatus.textContent = '';
    } catch (error) { aiStatus.textContent = error.message; }
  });
  aiProvider.addEventListener('change', () => {
    if (aiConfig) showAIProfile();
    aiStatus.textContent = '';
  });
  const aiForm = document.getElementById('aiForm');
  aiForm.addEventListener('input', () => { aiStatus.textContent = ''; });
  aiForm.addEventListener('submit', async event => {
    event.preventDefault();
    const testing = event.submitter === aiTest;
    const payload = {
      provider: aiProvider.value, model: aiModel.value, base_url: aiBase.value,
      api_key: aiKey.value, clear_key: aiClear.checked,
      backup_keys: testing ? '' : aiBackup.value, clear_backup_keys: !testing && aiClearBackup.checked
    };
    const enabled = [...aiForm.elements].filter(control => !control.disabled);
    enabled.forEach(control => { control.disabled = true; });
    aiStatus.textContent = testing ? 'Đang gọi API để kiểm tra…' : 'Đang lưu cấu hình…';
    let saved = false;
    try {
      const result = await editorRequest(testing ? '/api/ai_settings/test' : '/api/ai_settings', payload);
      if (!testing) { aiConfig = result; saved = true; }
      aiStatus.textContent = testing ? result.message : 'Đã lưu cấu hình AI.';
    } catch (error) { aiStatus.textContent = error.message; }
    finally {
      enabled.forEach(control => { control.disabled = false; });
      if (saved) showAIProfile();
    }
  });
  function editorAction(id, action) {
    const button = document.getElementById(id);
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { await action(); } catch (error) { editorStatus.textContent = error.message; }
      finally { button.disabled = false; }
    });
  }
  async function loadEditor({showCue = true} = {}) {
    if (editorDirty && !confirm('Thay phần sửa chưa lưu bằng SRT đã lưu?')) return false;
    const context = currentEditorContext();
    if (!context.video) throw new Error('Đợi video tải xong hoặc chọn video trước.');
    editorStatus.textContent = 'Đang tìm SRT khớp video…';
    const data = await editorRequest('/api/subtitles/load', context);
    if (JSON.stringify(context) !== JSON.stringify(currentEditorContext())) throw new Error('Video hoặc thư mục đã đổi. Bấm tải lại.');
    const loadedCues = Subtitles.parse(data.text);
    editorContext = context;
    editor.value = Subtitles.serialize(loadedCues);
    document.getElementById('subtitleSource').value = data.source;
    editorDirty = false;
    cues = loadedCues; rawValid = true; selectedCue = -1;
    renderCueList(); paintSubtitles();
    if (cues.length) {
      const current = Subtitles.active(cues, videoElement.currentTime || 0)[0];
      selectCue(current ?? 0, showCue && current === undefined);
      editorStatus.textContent = `Đã tải ${cues.length} câu từ ${data.loaded_path || data.path}. Bấm chữ trên video để sửa. Khi lưu: ${data.path}`;
    } else editorStatus.textContent = 'Chưa có SRT khớp video. Bấm + Câu mới tại đây để thêm chữ, rồi lưu SRT.';
    return true;
  }
  editorAction('loadSubtitles', loadEditor);
  editorAction('saveSubtitles', async () => {
    if (!editorContext || JSON.stringify(editorContext) !== JSON.stringify(currentEditorContext()))
      throw new Error('Hãy tải SRT cho video và nhóm nguồn hiện tại trước khi lưu.');
    const savedText = editor.value;
    await editorRequest('/api/subtitles/save', {...editorContext, text: savedText});
    editorDirty = editor.value !== savedText;
    editorStatus.textContent = 'Đã lưu phụ đề Việt.';
    await loadVideoList();
  });
  editorAction('useOcrText', () => {
    if (!state.lastOcrText || state.lastOcrVideo !== state.currentVideo) throw new Error('Hãy Test OCR cho video hiện tại trước.');
    instant.value = state.lastOcrText;
  });
  editorAction('translateText', async () => {
    const original = instant.value;
    editorStatus.textContent = 'Đang dịch qua AI…';
    const data = await editorRequest('/api/translate_text', {text: original});
    if (instant.value !== original) throw new Error('Văn bản đã đổi trong lúc dịch. Hãy dịch lại.');
    instant.value = data.text;
    editorStatus.textContent = 'Đã dịch; bấm Áp dụng cho câu đang chọn để đưa lên video.';
  });
  editorAction('textNow', () => {
    const time = videoElement.currentTime || 0;
    document.getElementById('textStart').value = time.toFixed(3);
    document.getElementById('textEnd').value = Math.min(time + 3, state.duration || time + 3).toFixed(3);
  });
  function cueFromFields() {
    const start = Number(document.getElementById('textStart').value);
    const end = Number(document.getElementById('textEnd').value);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > state.duration || !instant.value.trim())
      throw new Error('Nhập nội dung và thời gian trong video, kết thúc sau bắt đầu.');
    return {start, end, text: instant.value.trim()};
  }
  editorAction('appendText', async () => {
    if (!sameEditorContext() && !await loadEditor()) return;
    requireEditor();
    const start = videoElement.currentTime || 0;
    if (start >= state.duration) throw new Error('Tua tới trước cuối video để thêm câu.');
    cues.push({start, end: Math.min(start + 3, state.duration), text: 'Nhập phụ đề'});
    commitCues(); selectCue(cues.length - 1, true);
    overlay.querySelector(`[data-cue-index="${selectedCue}"]`)?.focus();
    editorStatus.textContent = 'Bấm vào chữ trên video để nhập. Lưu trước khi xuất.';
  });
  editorAction('updateCue', () => {
    requireEditor();
    if (!cues[selectedCue]) throw new Error('Chọn một câu trong danh sách hoặc trên video.');
    cues[selectedCue] = cueFromFields();
    commitCues(); selectCue(selectedCue, true);
    editorStatus.textContent = 'Đã cập nhật câu · chưa lưu.';
  });
  editorAction('deleteCue', () => {
    requireEditor();
    if (!cues[selectedCue]) throw new Error('Chọn câu cần xóa.');
    if (cues.length === 1) throw new Error('Cần giữ ít nhất một câu để xuất SRT.');
    cues.splice(selectedCue, 1);
    selectedCue = Math.min(selectedCue, cues.length - 1);
    commitCues(); selectCue(selectedCue, true);
    editorStatus.textContent = 'Đã xóa câu · chưa lưu.';
  });

  // --- OCR Test Execution ---
  async function testOcr() {
    if (!state.currentVideo) {
      alert('Vui lòng chọn video trước!');
      return;
    }

    btnTestOcr.disabled = true;
    ocrSpinner.style.display = 'inline-block';
    ocrResultText.textContent = 'Đang nhận diện qua RapidOCR...';

    saveCropToLocal(true);
    const payload = {
      video: state.currentVideo,
      time: videoElement.currentTime || 0,
      crop: [state.crop.ymin, state.crop.ymax, state.crop.xmin, state.crop.xmax],
      ocr_regions: ocrRegionConfig(),
      ocr_only: recognitionMode.value === 'ocr',
    };

    try {
      const res = await fetch('/api/test_ocr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Test OCR thất bại');

      if (data.crop_image) {
        ocrCropThumb.src = `data:image/jpeg;base64,${data.crop_image}`;
        ocrCropThumb.style.display = 'block';
        ocrEmptyPlaceholder.style.display = 'none';
      }

      if (data.text && data.text.trim()) {
        state.lastOcrText = data.text;
        state.lastOcrVideo = payload.video;
        ocrResultText.textContent = data.text;
        logMessage(`[OCR Test @ ${formatTime(payload.time)}] Tìm thấy: "${data.text}"`, 'info');
      } else {
        ocrResultText.textContent = payload.ocr_only
          ? '[Không tìm thấy dòng chữ trắng nằm ngang trong vùng OCR]'
          : '[Không phát hiện chữ trong vùng này]';
        logMessage(`[OCR Test @ ${formatTime(payload.time)}] Không tìm thấy chữ trong vùng chọn. Hãy tua tới frame có phụ đề và thử lại.`, 'warning');
      }
    } catch (err) {
      ocrResultText.textContent = `Lỗi: ${err.message}`;
      logMessage(`Lỗi OCR test: ${err.message}`, 'error');
    } finally {
      btnTestOcr.disabled = false;
      ocrSpinner.style.display = 'none';
    }
  }

  // --- Translation Runner ---
  async function startTranslate() {
    if (scanning || preparing || state.isTranslating) return;
    const videos = (state.selected.size ? [...state.selected] : [state.currentVideo]).filter(canSelect);
    if (!videos.length) return;
    if (editorDirty) { alert('Hãy lưu SRT đang sửa trước khi chạy hàng đợi.'); return; }

    const payload = {
      videos, folder: state.folder,
      mode: document.querySelector('input[name="outputMode"]:checked').value,
      no_ocr: recognitionMode.value === 'whisper',
      ocr_only: recognitionMode.value === 'ocr',
      output: outputFolder.value.trim() || 'outputs',
      video_ocr_regions: {},
    };
    preparing = true;
    updateStartButton();
    try {
      if (!payload.no_ocr) {
        for (const video of videos) {
          let regions = savedRegions(video);
          if (!regions) {
            setProcessStatus('running', `Đang dò vùng OCR · ${video.split('/').pop()}`);
            try { regions = await detectRegion(video, payload.folder); }
            catch (error) { logMessage(error.message, 'warning'); }
          }
          if (!regions) logMessage(`Chưa dò được vùng OCR: ${video}. Dùng vùng mặc định hoặc chỉnh vùng trên video trước khi chạy.`, 'warning');
          payload.video_ocr_regions[video] = ocrRegionConfig(regions || [fallbackCrop]);
        }
      }
      if (state.folder !== payload.folder || (outputFolder.value.trim() || 'outputs') !== payload.output)
        throw new Error('Thư mục nguồn hoặc đầu ra đã thay đổi.');
      btnStartTranslate.style.display = 'none';
      btnStopTranslate.style.display = 'inline-flex';
      setProcessStatus('running', 'Đang xử lý…');

      logMessage('========================================', 'system');
      logMessage(`Bắt đầu hàng đợi: ${payload.videos.length} video`, 'info');
      logMessage(`  Chế độ: ${payload.mode}`, 'info');
      logMessage(`  Đầu ra: ${payload.output}`, 'info');
      logMessage('  OCR: dùng vùng nhận chữ riêng cho từng video.', 'info');
      logMessage('========================================', 'system');

      const res = await fetch('/api/start_translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        await loadVideoList();
        throw new Error(data.error || 'Không thể khởi động translate.py');
      }

      connectLogStream();
    } catch (err) {
      logMessage(`Không thể khởi động dịch: ${err.message}`, 'error');
      // If error indicates running process, check if server is truly running or can be reset
      await checkStatus();
      if (statusText.textContent !== 'Đang xử lý…') {
        setProcessStatus('error', 'Lỗi khởi động');
        btnStartTranslate.style.display = 'inline-flex';
        btnStopTranslate.style.display = 'none';
      }
    } finally {
      preparing = false;
      updateStartButton();
    }
  }

  async function stopTranslate() {
    try {
      btnStopTranslate.disabled = true;
      const res = await fetch('/api/stop_translate', { method: 'POST' });
      const data = await res.json();
      logMessage('Đã dừng tiến trình dịch.', 'warning');
      await checkStatus();
    } catch (err) {
      logMessage(`Lỗi khi dừng: ${err.message}`, 'error');
    } finally {
      btnStopTranslate.disabled = false;
    }
  }

  async function resetProcess() {
    try {
      const res = await fetch('/api/reset_process_status', { method: 'POST' });
      const data = await res.json();
      await checkStatus();
      logMessage('Đã yêu cầu dừng và giải phóng hàng đợi.', 'system');
    } catch (err) {
      logMessage(`Lỗi khi reset: ${err.message}`, 'error');
    }
  }

  function connectLogStream() {
    if (state.eventSource) {
      state.eventSource.close();
    }

    state.eventSource = new EventSource('/api/logs');
    state.eventSource.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'log') {
          logMessage(msg.text, msg.level || 'info');
        } else if (msg.type === 'status') {
          renderProgress(msg.items);
          handleStatusChange(msg.status);
          if (msg.status !== 'running') loadVideoList();
        }
      } catch (err) {
        logMessage(e.data);
      }
    };

    state.eventSource.onerror = () => {
      checkStatus();
    };
  }

  async function checkStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      renderProgress(data.items);
      handleStatusChange(data.status);
    } catch (err) {}
  }

  function handleStatusChange(status) {
    state.isTranslating = status === 'running';
    if (status === 'running') {
      setProcessStatus('running', 'Đang xử lý…');
      btnStartTranslate.style.display = 'none';
      btnStopTranslate.style.display = 'inline-flex';
    } else {
      btnStartTranslate.style.display = 'inline-flex';
      btnStopTranslate.style.display = 'none';
      if (status === 'completed') {
        setProcessStatus('success', 'Hoàn tất');
        logMessage('🎉 Tiến trình dịch hoàn tất thành công!', 'success');
      } else if (status === 'failed') {
        setProcessStatus('error', 'Thất bại');
        logMessage('❌ Tiến trình dịch kết thúc có lỗi.', 'error');
      } else if (status === 'stopped') {
        setProcessStatus('idle', 'Đã dừng');
        logMessage('⏹ Tiến trình đã dừng hoàn toàn. Bạn có thể bắt đầu lại.', 'warning');
      } else {
        setProcessStatus('idle', 'Sẵn sàng');
      }
      if (state.eventSource) {
        state.eventSource.close();
        state.eventSource = null;
      }
    }
  }

  function setProcessStatus(type, text) {
    statusDot.className = `status-dot status-${type}`;
    statusText.textContent = text;
  }

  function logMessage(text, level = 'system') {
    const line = document.createElement('div');
    line.className = `terminal-line log-${level}`;
    line.textContent = text;
    terminalLogs.appendChild(line);
    while (terminalLogs.childElementCount > 500) terminalLogs.firstElementChild.remove();
    terminalLogs.scrollTop = terminalLogs.scrollHeight;
  }

  // --- Bind All Events ---
  function bindEvents() {
    document.getElementById('ocrRegionSelect').addEventListener('change', event => {
      editOnVideo.checked = false; paintSubtitles();
      state.activeRegion = Number(event.target.value);
      state.crop = {...state.regions[state.activeRegion]};
      renderCropBox(); updateCoordsInputs();
    });
    document.getElementById('btnAddOcrRegion').addEventListener('click', () => {
      editOnVideo.checked = false; paintSubtitles();
      state.activeRegion = state.regions.length;
      state.crop = {...PRESETS.top};
      saveCropToLocal(true); updateRegionSelect(); renderCropBox(); updateCoordsInputs();
    });
    document.getElementById('btnDeleteOcrRegion').addEventListener('click', () => {
      if (state.regions.length <= 1) return;
      state.regions.splice(state.activeRegion, 1);
      applyRegions(state.regions);
      saveCropToLocal(true);
    });
    document.getElementById('btnSelectAll').addEventListener('click', () => {
      state.selected = new Set(state.videos.filter(canQueue).map(v => v.path));
      renderVideoQueue();
    });
    document.getElementById('btnClearSelection').addEventListener('click', () => {
      state.selected.clear();
      renderVideoQueue();
    });
    videoSelect.addEventListener('change', (e) => {
      loadVideo(e.target.value);
    });

    btnRefreshVideos.addEventListener('click', loadVideoList);

    document.getElementById('btnChooseFolder').addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const response = await fetch('/api/choose_folder', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Không chọn được thư mục');
        if (!data.folder) return;
        if (data.folder !== state.folder) {
          if (editorDirty && !confirm('Đổi thư mục sẽ bỏ phần sửa chưa lưu. Tiếp tục?')) return;
          editorDirty = false;
          state.videos = [];
          state.selected.clear();
          loadVideo('');
          state.folder = data.folder;
          localStorage.setItem(STORAGE_KEYS.SOURCE_FOLDER, data.folder);
        }
        await loadVideoList();
      } catch (error) {
        logMessage(error.message, 'error');
      } finally {
        button.disabled = false;
      }
    });

    btnPlayPause.addEventListener('click', () => {
      if (videoElement.paused) videoElement.play();
      else videoElement.pause();
    });

    timelineSlider.addEventListener('input', (e) => {
      videoElement.currentTime = parseFloat(e.target.value);
      updateTimeDisplay(videoElement.currentTime);
    });

    btnStepBack.addEventListener('click', () => { videoElement.currentTime = Math.max(0, videoElement.currentTime - 1); });
    btnStepForward.addEventListener('click', () => { videoElement.currentTime = Math.min(state.duration, videoElement.currentTime + 1); });
    btnStepBackLarge.addEventListener('click', () => { videoElement.currentTime = Math.max(0, videoElement.currentTime - 5); });
    btnStepForwardLarge.addEventListener('click', () => { videoElement.currentTime = Math.min(state.duration, videoElement.currentTime + 5); });

    document.querySelectorAll('.btn-preset').forEach((btn) => {
      btn.addEventListener('click', () => {
        applyPreset(btn.dataset.preset);
      });
    });

    btnResetCrop.addEventListener('click', () => {
      const savedDef = localStorage.getItem(STORAGE_KEYS.DEFAULT_CROP);
      if (savedDef) {
        try {
          const parsed = JSON.parse(savedDef);
          if (isValidCrop(parsed)) {
            state.crop = { ...parsed };
            renderCropBox();
            updateCoordsInputs();
            saveCropToLocal(true);
            showSavedBadge('Đã khôi phục toạ độ mặc định');
            return;
          }
        } catch (e) {}
      }
      applyPreset('bottom_standard');
    });

    if (btnSaveDefaultCrop) {
      btnSaveDefaultCrop.addEventListener('click', () => {
        try {
          fallbackCrop = {...state.crop};
          localStorage.setItem(STORAGE_KEYS.DEFAULT_CROP, JSON.stringify(state.crop));
          localStorage.setItem(STORAGE_KEYS.LAST_CROP, JSON.stringify(state.crop));
          if (state.currentVideo) {
            localStorage.setItem(STORAGE_KEYS.VIDEO_CROP_PREFIX + state.currentVideo, JSON.stringify(state.crop));
          }
          saveCropToLocal(true);
          showSavedBadge('✅ Đã ghi nhớ toạ độ mặc định');
          logMessage(`Đã ghi nhớ toạ độ mặc định: Y [${(state.crop.ymin*100).toFixed(1)}% - ${(state.crop.ymax*100).toFixed(1)}%], X [${(state.crop.xmin*100).toFixed(1)}% - ${(state.crop.xmax*100).toFixed(1)}%]`, 'success');
        } catch (e) {
          logMessage(`Lỗi ghi nhớ toạ độ: ${e.message}`, 'error');
        }
      });
    }

    btnDefaultOutput.addEventListener('click', () => chipSeriesFolder.click());

    outputFolder.addEventListener('input', () => { state.useSeriesOutput = false; });
    outputFolder.addEventListener('change', loadVideoList);

    document.querySelectorAll('.chip-sample').forEach(chip => {
      chip.addEventListener('click', () => {
        state.useSeriesOutput = chip.dataset.sample === 'series';
        if (!state.useSeriesOutput) outputFolder.value = chip.dataset.sample;
        updateOutputPreview();
        loadVideoList();
      });
    });

    document.querySelectorAll('input[name="outputMode"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        state.selected = new Set([...state.selected].filter(canSelect));
        renderVideoQueue();
      });
    });

    [inputYmin, inputYmax, inputXmin, inputXmax].forEach((inp) => {
      inp.addEventListener('input', readCoordsInputs);
    });

    btnTestOcr.addEventListener('click', testOcr);
    btnStartTranslate.addEventListener('click', startTranslate);
    btnStopTranslate.addEventListener('click', stopTranslate);

    if (btnResetProcess) {
      btnResetProcess.addEventListener('click', resetProcess);
    }

    btnClearLog.addEventListener('click', () => { terminalLogs.innerHTML = ''; });
    btnCopyLog.addEventListener('click', () => {
      navigator.clipboard.writeText(terminalLogs.innerText).then(() => {
        alert('Đã sao chép toàn bộ logs vào clipboard!');
      });
    });
  }

  // Run on DOM ready
  document.addEventListener('DOMContentLoaded', init);
})();
