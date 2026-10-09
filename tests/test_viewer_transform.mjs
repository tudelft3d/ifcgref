import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const template = readFileSync(new URL('../templates/view3D.html', import.meta.url), 'utf8');
const threeSource = readFileSync(new URL('../templates/build/three.module.js', import.meta.url));
const THREE = await import(`data:text/javascript;base64,${threeSource.toString('base64')}`);

function viewer(functionNames, globals) {
    const context = vm.createContext({ THREE, ...globals });
    // Execute the actual template functions, without its browser startup or Jinja values.
    const functions = functionNames.map(name => {
        const match = template.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }`, 'm'));
        assert.ok(match, `Template function ${name} must exist`);
        return match[0];
    });
    vm.runInContext(`'use strict';\n${functions.join('\n')}`, context);
    return context;
}

const transformState = () => ({ translateX: 10, translateY: 20, translateZ: 30, verticalScale: 11 });

test('Mercator matrix maps IFC axes from loader X, Z, -Y and retains cross terms', () => {
    const context = viewer(['getBimMercatorMatrix'], {
        mapAxes: [[2, 3], [5, 7]], modelTransformState: transformState(), verticalOffset: 0,
    });
    const matrix = context.getBimMercatorMatrix();
    for (const [loaderPoint, expected] of [
        [[0, 0, 0], [10, 20, 30]],
        [[1, 0, 0], [12, 23, 30]], // IFC +X
        [[0, 0, -1], [15, 27, 30]], // IFC +Y
        [[0, 1, 0], [10, 20, 41]], // IFC +Z
        [[4, 6, -9], [63, 95, 96]],
    ]) {
        assert.deepEqual(new THREE.Vector3(...loaderPoint).applyMatrix4(matrix).toArray(), expected);
    }
});

test('vertical offset is scaled once, independently of horizontal map axes', () => {
    const context = viewer(['getBimMercatorMatrix'], {
        mapAxes: [[2, 3], [5, 7]], modelTransformState: transformState(), verticalOffset: 2,
    });
    assert.deepEqual(
        new THREE.Vector3(4, 6, -9).applyMatrix4(context.getBimMercatorMatrix()).toArray(),
        [63, 95, 118],
    );
});

test('vertical modes restore origin elevation or put the rebased minimum on the ground', () => {
    let selected = { value: '0' };
    let repaints = 0;
    let renders = 0;
    const context = viewer(['getBimMercatorMatrix', 'applyVerticalMode'], {
        mapAxes: [[2, 3], [5, 7]], modelTransformState: transformState(),
        viewerOrigin: [600000, 4000000, 30], lz: -7, verticalMode: '0', verticalOffset: 0,
        document: { querySelector: () => selected },
        map: { triggerRepaint: () => repaints++ }, render: () => renders++,
    });
    for (const [selection, mode, offset, minimumZ] of [
        [{ value: '0' }, '0', 30, 283],
        [{ value: '1' }, '1', 7, 30],
        [null, '0', 30, 283],
    ]) {
        selected = selection;
        context.applyVerticalMode();
        assert.equal(context.verticalMode, mode);
        assert.equal(context.verticalOffset, offset);
        assert.equal(new THREE.Vector3(0, -7, 0).applyMatrix4(context.getBimMercatorMatrix()).z, minimumZ);
    }
    assert.equal(repaints, 3);
    assert.equal(renders, 3);
});

test('custom layer uses the same matrix and clips at original IFC zero elevation', () => {
    const renderer = { resetState() {}, render() {} };
    const context = viewer(['getBimMercatorMatrix', 'makeCustomLayer'], {
        mapAxes: [[2, 3], [5, 7]], modelTransformState: transformState(),
        viewerOrigin: [600000, 4000000, 30], verticalOffset: 30, verticalMode: '0',
        scene: new THREE.Scene(), renderer,
    });
    const layer = context.makeCustomLayer();
    layer.onAdd({ triggerRepaint() {} }, null);
    layer.render(null, new THREE.Matrix4().makeTranslation(1, 2, 3).toArray());
    assert.deepEqual(
        new THREE.Vector3(4, 6, -9).applyMatrix4(layer.camera.projectionMatrix).toArray(),
        [64, 97, 429],
    );
    assert.equal(renderer.localClippingEnabled, true);
    assert.equal(renderer.clippingPlanes[0].distanceToPoint(new THREE.Vector3(0, -30, 0)), 0);
    context.verticalMode = '1';
    layer.render(null, new THREE.Matrix4().toArray());
    assert.equal(renderer.localClippingEnabled, false);
    assert.equal(renderer.clippingPlanes.length, 0);
});

test('each IFC load disables automatic coordination and sets the metre anchor before loading', async () => {
    const events = [];
    const origin = [600000, 4000000, 30];
    const status = { style: {} };
    const toggle = {};
    let categories;
    let configuration;
    let coordination;
    class Loader {
        ifcManager = {
            setWasmPath() { events.push('wasm'); },
            parser: { setupOptionalCategories(value) { categories = value; events.push('categories'); } },
            applyWebIfcConfig(value) { configuration = value; events.push('config'); },
            setupCoordinationMatrix(value) { coordination = value; events.push('matrix'); },
        };
        load(url) {
            events.push('load');
            assert.equal(url, '/uploads/model.ifc');
            assert.equal(configuration.COORDINATE_TO_ORIGIN, false);
            assert.deepEqual(new THREE.Vector3(origin[0], origin[2], -origin[1])
                .applyMatrix4(coordination).toArray(), [0, 0, 0]);
            assert.deepEqual(new THREE.Vector3(origin[0] + 0.125, origin[2] + 0.25, -origin[1] - 0.5)
                .applyMatrix4(coordination).toArray(), [0.125, 0.25, -0.5]);
        }
    }
    const context = viewer(['loadIfcModel'], {
        IFCLoader: Loader, IFCSPACE: 3856911033, viewerOrigin: origin, fn: 'model.ifc',
        ifcLoadVersion: 0, ifcLoader: null,
        document: { getElementById: id => id === 'loadingStatus' ? status : toggle },
        removeCurrentIfcModel() { events.push('remove'); },
    });
    for (const includeSpaces of [false, true]) {
        events.length = 0;
        coordination = undefined;
        await context.loadIfcModel(includeSpaces);
        assert.deepEqual(events, ['remove', 'wasm', 'categories', 'config', 'matrix', 'load']);
        assert.equal(categories[3856911033], includeSpaces);
    }
    assert.equal(context.ifcLoadVersion, 2);
});
