import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const template = readFileSync(new URL('../templates/view3D.html', import.meta.url), 'utf8');
const start = template.indexOf('    const cartoQuery =');
const end = template.indexOf('    const viewerControls =', start);
assert.ok(start >= 0 && end > start, 'Viewer map configuration must exist');

function mapStyles(cartoKey = '', mapTilerStyles = []) {
    return vm.runInNewContext(`${template.slice(start, end)}\nmapStyles;`, {
        cartoKey, mapTilerStyles, URLSearchParams,
    });
}

test('without provider keys, OpenStreetMap is the default and CARTO is unavailable', () => {
    const styles = mapStyles();
    assert.equal(styles[0].id, 'openstreetmap');
    assert.ok(styles.every(style => !style.id.startsWith('carto-')));
    const source = styles[0].style.sources.openstreetmap;
    assert.equal(source.tiles[0], 'https://tile.openstreetmap.org/{z}/{x}/{y}.png');
    assert.equal(source.maxzoom, 19);
    assert.match(source.attribution, /openstreetmap.org\/copyright/);
    assert.ok(styles.find(style => style.id === 'pdok-brt-parcels').overlays.length);
});

test('configured CARTO styles send the encoded key on every tile URL', () => {
    const key = 'fake key&+/#?';
    const styles = mapStyles(key);
    assert.equal(styles[0].id, 'carto-voyager');
    const cartoStyles = styles.filter(style => style.id.startsWith('carto-'));
    assert.equal(cartoStyles.length, 2);
    for (const style of cartoStyles) {
        const source = style.style.sources[style.id];
        for (const tile of source.tiles) {
            const url = new URL(tile);
            assert.equal(url.hostname, 'basemaps.cartocdn.com');
            assert.equal(url.searchParams.get('key'), key);
            assert.equal(url.hash, '');
        }
        assert.match(source.attribution, /CARTO/);
        assert.match(source.attribution, /OpenStreetMap/);
    }
});

test('MapTiler options retain the default and do not displace PDOK layers', () => {
    const mapTiler = [{ id: 'streets', label: 'MapTiler Streets', style: 'configured-style' }];
    for (const key of ['', 'fake-carto-key']) {
        const styles = mapStyles(key, mapTiler);
        assert.equal(styles[0].id, key ? 'carto-voyager' : 'openstreetmap');
        assert.equal(styles.find(style => style.id === 'streets').style, 'configured-style');
        assert.ok(styles.some(style => style.id === 'pdok-brt'));
    }
});
