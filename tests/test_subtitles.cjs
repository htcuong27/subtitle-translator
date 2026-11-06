const assert = require('node:assert/strict');
const subtitles = require('../web/subtitles.js');
const original = '7\r\n00:00:01,250 --> 00:00:02,500\r\nXin chào\r\ndòng hai\r\n\r\n8\r\n00:00:02,000 --> 00:00:03,000\r\nCâu 2';
const cues = subtitles.parse(original);
assert.equal(cues[0].start, 1.25);
assert.equal(cues[0].text, 'Xin chào\ndòng hai');
assert.deepEqual(subtitles.active(cues, 2), [0, 1]);
assert.deepEqual(subtitles.active(cues, 2.5), [1]);
assert.deepEqual(subtitles.active(cues, 3), []);
cues[0].text = '<script>text only</script>\nđã sửa';
assert.deepEqual(subtitles.parse(subtitles.serialize(cues)), cues);
assert.equal(subtitles.timestamp(59.9999), '00:01:00,000');
assert.deepEqual(subtitles.parse(''), []);
for (const bad of ['broken', '1\n00:00:61,000 --> 00:01:02,000\nBad', '1\n00:00:03,000 --> 00:00:02,000\nBad']) {
  assert.throws(() => subtitles.parse(bad));
}
console.log('Subtitle parsing, live timing, overlap and edited SRT roundtrip passed');

assert.deepEqual(subtitles.parse('1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:02,000 --> 00:00:03,000\nCó chữ').map(cue => cue.text), ['Có chữ']);
