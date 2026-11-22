const OPTICS_KEY = 'guangzai.optics.v1';
const ANIMATIONS = '/animations/manifest.json';
const LOOPING = new Set(['idle', 'sleep', 'focus', 'bored', 'think']);
const IDLE_MOTIONS = {
  idle_glance: 'idle', idle_lean: 'idle', idle_hop: 'idle',
  sleep_shift: 'sleep', focus_stretch: 'focus',
};
const ONE_SHOTS = new Set([
  'pat', 'feed', 'play', 'happy', 'sad', 'look', 'look-left', 'look-right',
  'wave', 'wave-left', 'wave-right',
  ...Object.keys(IDLE_MOTIONS),
]);
const CLIP_NAME = /^[a-zA-Z0-9_-]+\.(mp4|webm)$/;
const IMAGE_NAME = /^[a-zA-Z0-9_-]+\.(png|webp)$/;

function readClips(clips) {
  return Object.fromEntries(Object.entries(clips || {}).filter(([name, clip]) =>
    (LOOPING.has(name) || ONE_SHOTS.has(name)) && typeof clip === 'string' && CLIP_NAME.test(clip)
  ));
}

export class Display {
  constructor({ optics, character, video, form, decor, sceneArt }) {
    this.optics = optics;
    this.character = character;
    this.video = video;
    this.form = form;
    this.decor = decor;
    this.sceneArt = sceneArt;
    this.clips = {};
    this.scarfClips = {};
    this.sceneImage = '';
    this.failed = new Set();
    this.scene = { state: 'idle', direction: '' };
    this.appearance = { room: 'plain', outfit: 'plain' };
    this.source = '';
    this.loadOptics();
    form.addEventListener('input', () => this.applyOptics());
    form.addEventListener('reset', () => setTimeout(() => this.applyOptics(), 0));
    window.addEventListener('storage', (event) => {
      if (event.key === OPTICS_KEY && event.newValue) this.loadOptics();
    });
    video.addEventListener('playing', () => {
      if (video.currentSrc !== video.src || !this.source) return;
      video.hidden = false;
      character.hidden = true;
    });
    video.addEventListener('error', () => {
      if (this.source && video.error) this.fallback(this.source);
    });
    sceneArt.addEventListener('load', () => { sceneArt.hidden = false; });
    sceneArt.addEventListener('error', () => { sceneArt.hidden = true; });
  }

  async loadClips() {
    try {
      const response = await fetch(ANIMATIONS, { cache: 'no-cache' });
      if (!response.ok) return;
      const manifest = await response.json();
      this.clips = readClips(manifest.clips);
      this.scarfClips = readClips(manifest.outfits?.scarf);
      const study = manifest.scenes?.study;
      this.sceneImage = typeof study === 'string' && IMAGE_NAME.test(study) ? study : '';
      this.show(this.scene.state, this.scene.direction);
    } catch (error) {
      console.warn('动画配置读取失败', error.message);
    }
  }

  show(state, direction = '', appearance = this.appearance) {
    this.scene = { state, direction };
    this.appearance = appearance;
    this.character.dataset.state = state;
    this.character.dataset.direction = direction;
    this.character.dataset.outfit = appearance.outfit;
    this.updateRoom(appearance.room);
    const directional = direction && `${state}-${direction}`;
    const scarf = appearance.outfit === 'scarf' ? this.scarfClips : {};
    const requested = [scarf[directional], scarf[state], this.clips[directional], this.clips[state]]
      .find((clip) => clip && !this.failed.has(clip));
    this.play(requested || this.baseClip(state, scarf), !requested || LOOPING.has(state));
  }

  updateRoom(room) {
    this.decor.dataset.room = room;
    const url = room === 'study' && this.sceneImage ? `/animations/${this.sceneImage}` : '';
    if ((this.sceneArt.getAttribute('src') || '') === url) return;
    this.sceneArt.hidden = true;
    if (url) this.sceneArt.src = url;
    else this.sceneArt.removeAttribute('src');
  }

  baseClip(state, outfit) {
    const base = IDLE_MOTIONS[state];
    return [outfit[base], this.clips[base], outfit.idle, this.clips.idle]
      .find((clip) => clip && !this.failed.has(clip));
  }

  fallback(clip) {
    this.failed.add(clip);
    this.show(this.scene.state, this.scene.direction);
  }

  play(clip, loop) {
    this.video.loop = loop;
    if ((clip || '') === this.source) {
      if (clip && this.video.ended) this.video.play().catch((error) => {
        if (error.name !== 'AbortError' && this.source === clip) this.fallback(clip);
      });
      return;
    }
    this.video.pause();
    this.source = clip || '';
    if (!clip) {
      this.video.hidden = true;
      this.character.hidden = false;
      this.video.removeAttribute('src');
      this.video.load();
      return;
    }
    this.video.src = `/animations/${clip}`;
    this.video.play().catch((error) => {
      if (error.name === 'AbortError' || this.source !== clip) return;
      this.fallback(clip);
    });
  }

  loadOptics() {
    try {
      const saved = JSON.parse(localStorage.getItem(OPTICS_KEY) || '{}');
      for (const input of this.form.elements) {
        if (!Object.hasOwn(saved, input.name)) continue;
        if (input.type === 'checkbox') input.checked = saved[input.name] === true;
        else input.value = saved[input.name];
      }
    } catch { /* Defaults also work in private browsing. */ }
    this.applyOptics();
  }

  applyOptics() {
    const { flipX, flipY, rotation, x, y, scale } = this.form.elements;
    const zoom = Number(scale.value) / 100;
    const style = this.optics.style;
    style.setProperty('--scale-x', zoom * (flipX.checked ? -1 : 1));
    style.setProperty('--scale-y', zoom * (flipY.checked ? -1 : 1));
    style.setProperty('--move-x', `${x.value}%`);
    style.setProperty('--move-y', `${y.value}%`);
    style.setProperty('--rotation', `${Number(rotation.value)}deg`);
    try {
      localStorage.setItem(OPTICS_KEY, JSON.stringify({
        flipX: flipX.checked, flipY: flipY.checked, rotation: rotation.value,
        x: x.value, y: y.value, scale: scale.value,
      }));
    } catch { /* Keep this session's calibration. */ }
  }
}
