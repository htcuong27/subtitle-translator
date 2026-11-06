/* Shared subtitle values for the direct video editor and SRT export. */
(function (root) {
  const timestamp = seconds => {
    const ms = Math.round(seconds * 1000);
    return `${String(Math.floor(ms / 3600000)).padStart(2, '0')}:${String(Math.floor(ms / 60000) % 60).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')},${String(ms % 1000).padStart(3, '0')}`;
  };
  function parse(text) {
    if (!text.trim()) return [];
    return text.trim().split(/\r?\n\s*\r?\n/).map((block, index) => {
      const lines = block.split(/\r?\n/);
      const match = /^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3}) --> (\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$/.exec(lines[1] || '');
      if (!match) throw Error(`Câu ${index + 1}: SRT không hợp lệ`);
      const values = match.slice(1).map(Number);
      const seconds = offset => values[offset] * 3600 + values[offset + 1] * 60 + values[offset + 2] + values[offset + 3] / 1000;
      const cue = {start: seconds(0), end: seconds(4), text: lines.slice(2).join('\n')};
      if (cue.end <= cue.start) throw Error(`Câu ${index + 1}: kết thúc phải sau bắt đầu`);
      return cue;
    }).filter(cue => cue.text.trim());
  }
  const serialize = cues => cues.map((cue, i) => `${i + 1}\n${timestamp(cue.start)} --> ${timestamp(cue.end)}\n${cue.text}\n`).join('\n');
  const active = (cues, time) => cues.flatMap((cue, i) => time >= cue.start && time < cue.end ? [i] : []);
  const api = {parse, serialize, timestamp, active};
  if (typeof module !== 'undefined') module.exports = api;
  else root.Subtitles = api;
})(typeof window === 'undefined' ? globalThis : window);
