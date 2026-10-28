// Rigid-body physics demo for the viewer, using the Rapier engine (WASM, @dimforge/rapier3d-compat 0.14).
//
// What is real: gravity (9.81 m/s^2), rigid-body dynamics, contact solving, friction/restitution, mass from the
// user's input, and collision against the ACTUAL triangle mesh of a static object (the parent, e.g. the table).
// What is approximate, and said so on screen: a dynamic object's collider is the CONVEX HULL of its mesh (a mug's
// handle hole and its hollow interior are filled in), and sizes are only physical if the scene is calibrated.
//
// Isolation: the simulation never touches the edited objects. It clones each object's visual (sharing geometry) into
// a separate group, hides the originals while running, and Reset deletes the clones and shows the originals again
// with their transforms exactly as they were.
import RAPIER from "/vendor/rapier/rapier.es.js";
import { ConvexGeometry } from "three/addons/geometries/ConvexGeometry.js";

const GRAVITY = 9.81;
const DT = 1 / 60;

let rapierReady = null;
export function initRapier() {
  if (!rapierReady) rapierReady = RAPIER.init().then(() => RAPIER);
  return rapierReady;
}

export function createPhysics({ THREE, scene, getObjects, getCmPerUnit, onStart, onStop }) {
  const simRoot = new THREE.Group();
  simRoot.name = "physics_sim_root";
  scene.add(simRoot);

  let world = null;
  let running = false;
  let bodies = []; // { obj, body, proxy, hullMesh, kind, massKg }
  let staticMeta = null;
  let steps = 0;
  let accumulator = 0;
  let settledAtStep = null;
  let calm = 0;
  let config = null;

  // body-frame vertices (meters) of everything under `group`, i.e. relative to the group's origin, un-rotated
  function bodyFrameVertices(group, metersPerUnit) {
    group.updateWorldMatrix(true, true);
    const origin = new THREE.Vector3().setFromMatrixPosition(group.matrixWorld);
    const q = new THREE.Quaternion();
    group.matrixWorld.decompose(new THREE.Vector3(), q, new THREE.Vector3());
    const qi = q.clone().invert();
    const v = new THREE.Vector3();
    const out = [];
    group.traverse((o) => {
      if (!o.isMesh) return;
      const pos = o.geometry.attributes.position;
      for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld).sub(origin).applyQuaternion(qi).multiplyScalar(metersPerUnit);
        out.push(v.x, v.y, v.z);
      }
    });
    return new Float32Array(out);
  }

  function worldMesh(group, metersPerUnit) {
    group.updateWorldMatrix(true, true);
    const verts = [];
    const idx = [];
    const v = new THREE.Vector3();
    group.traverse((o) => {
      if (!o.isMesh) return;
      const pos = o.geometry.attributes.position;
      const base = verts.length / 3;
      for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld).multiplyScalar(metersPerUnit);
        verts.push(v.x, v.y, v.z);
      }
      if (o.geometry.index) for (let i = 0; i < o.geometry.index.count; i++) idx.push(base + o.geometry.index.getX(i));
      else for (let i = 0; i < pos.count; i++) idx.push(base + i);
    });
    return { vertices: new Float32Array(verts), indices: new Uint32Array(idx) };
  }

  function worldBoxMeters(group, metersPerUnit) {
    const b = new THREE.Box3().setFromObject(group);
    return { min: b.min.clone().multiplyScalar(metersPerUnit), max: b.max.clone().multiplyScalar(metersPerUnit) };
  }

  async function start(opts = {}) {
    await initRapier();
    reset();
    const o = { autoRun: true, dropHeightCm: 15, massKg: 0.3, showColliders: true, friction: 0.6, restitution: 0.1, staticId: null, ...opts };
    config = o;
    const objs = getObjects().filter((x) => !x.missing);
    if (!objs.length) throw new Error("no objects loaded to simulate");
    const cmPerUnit = getCmPerUnit();
    const m = cmPerUnit / 100; // meters per scene unit
    world = new RAPIER.World({ x: 0, y: -GRAVITY, z: 0 });
    world.timestep = DT;

    let staticObj = o.staticId ? objs.find((x) => x.id === o.staticId) : objs.length > 1 ? objs.find((x) => x.role === "parent") || objs[0] : null;
    const dynamics = objs.filter((x) => x !== staticObj);
    staticMeta = null;

    if (staticObj) {
      const tm = worldMesh(staticObj.group, m);
      const sb = world.createRigidBody(RAPIER.RigidBodyDesc.fixed());
      world.createCollider(RAPIER.ColliderDesc.trimesh(tm.vertices, tm.indices).setFriction(o.friction).setRestitution(o.restitution), sb);
      const box = worldBoxMeters(staticObj.group, m);
      staticMeta = { id: staticObj.id, collider: "triangle mesh (exact, static)", triangles: tm.indices.length / 3, top_y_m: box.max.y };
      staticObj.group.visible = false;
      const proxy = staticObj.group.clone(true);
      proxy.userData = {};
      proxy.visible = true; // clone() copied the original's visible=false
      simRoot.add(proxy);
      bodies.push({ obj: staticObj, body: null, proxy, isStatic: true });
    } else {
      // single object: a ground plane at the object's current lowest point, object is lifted by the drop height
      const box = worldBoxMeters(dynamics[0].group, m);
      const gb = world.createRigidBody(RAPIER.RigidBodyDesc.fixed().setTranslation(0, box.min.y - 0.5, 0));
      world.createCollider(RAPIER.ColliderDesc.cuboid(50, 0.5, 50).setFriction(o.friction).setRestitution(o.restitution), gb);
      staticMeta = { id: "ground plane", collider: "infinite-ish cuboid slab", top_y_m: box.min.y };
    }

    for (const obj of dynamics) {
      const g = obj.group;
      g.updateWorldMatrix(true, true);
      const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
      g.matrixWorld.decompose(p, q, s);
      const pts = bodyFrameVertices(g, m);
      let desc = RAPIER.ColliderDesc.convexHull(pts);
      let kind = "convex hull of the mesh";
      if (!desc) {
        const bb = new THREE.Box3().setFromObject(g).getSize(new THREE.Vector3()).multiplyScalar(m / 2);
        desc = RAPIER.ColliderDesc.cuboid(bb.x, bb.y, bb.z);
        kind = "bounding box (convex hull failed)";
      }
      desc.setMass(o.massKg).setFriction(o.friction).setRestitution(o.restitution);
      const body = world.createRigidBody(
        RAPIER.RigidBodyDesc.dynamic()
          .setTranslation(p.x * m, p.y * m + o.dropHeightCm / 100, p.z * m)
          .setRotation({ x: q.x, y: q.y, z: q.z, w: q.w })
          .setCanSleep(true)
          .setCcdEnabled(true)
      );
      world.createCollider(desc, body);
      g.visible = false;
      const proxy = g.clone(true);
      proxy.userData = {};
      proxy.visible = true;
      simRoot.add(proxy);

      let hullMesh = null;
      if (o.showColliders) {
        const hp = [];
        for (let i = 0; i < pts.length; i += 3) hp.push(new THREE.Vector3(pts[i] / m, pts[i + 1] / m, pts[i + 2] / m));
        try {
          const geo = new ConvexGeometry(hp);
          hullMesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x35d0ff, wireframe: true, transparent: true, opacity: 0.5 }));
          proxy.add(hullMesh);
          hullMesh.scale.set(1 / s.x, 1 / s.y, 1 / s.z); // proxy carries the object scale; the hull is already in scene units
        } catch (e) { hullMesh = null; }
      }
      bodies.push({ obj, body, proxy, hullMesh, kind, massKg: body.mass(), startY: p.y * m + o.dropHeightCm / 100 });
    }
    steps = 0; accumulator = 0; settledAtStep = null; calm = 0;
    running = true;
    sync();
    if (onStart) onStart();
    return state();
  }

  function sync() {
    const m = getCmPerUnit() / 100;
    for (const b of bodies) {
      if (b.isStatic) {
        b.proxy.position.copy(b.obj.group.position); b.proxy.quaternion.copy(b.obj.group.quaternion); b.proxy.scale.copy(b.obj.group.scale);
        continue;
      }
      const t = b.body.translation(), r = b.body.rotation();
      b.proxy.position.set(t.x / m, t.y / m, t.z / m);
      b.proxy.quaternion.set(r.x, r.y, r.z, r.w);
      b.proxy.scale.copy(b.obj.group.scale);
    }
  }

  function stepN(n) {
    if (!world) throw new Error("simulation not started");
    for (let i = 0; i < n; i++) {
      world.step();
      steps += 1;
      const dyn = bodies.filter((b) => !b.isStatic);
      const quiet = dyn.every((b) => b.body.isSleeping() || (len(b.body.linvel()) < 0.01 && len(b.body.angvel()) < 0.05));
      calm = quiet ? calm + 1 : 0;
      if (settledAtStep === null && calm >= 30) settledAtStep = steps;
    }
    sync();
  }

  const len = (v) => Math.hypot(v.x, v.y, v.z);

  function tick(dtSec) {
    if (!running || !world || !(config && config.autoRun)) return;
    accumulator += Math.min(dtSec, 0.1);
    let n = 0;
    while (accumulator >= DT && n < 6) { accumulator -= DT; n += 1; }
    if (n) stepN(n);
  }

  function reset() {
    for (const b of bodies) {
      b.obj.group.visible = true;
      if (b.proxy) { simRoot.remove(b.proxy); b.proxy.traverse((o) => { if (o.isMesh && o === b.hullMesh) { o.geometry.dispose(); o.material.dispose(); } }); }
    }
    bodies = [];
    if (world) { world.free(); world = null; }
    const was = running;
    running = false; staticMeta = null;
    if (was && onStop) onStop();
  }

  function state() {
    const m = getCmPerUnit() / 100;
    return {
      proxies_visible: bodies.every((b) => b.proxy.visible), running, steps, sim_time_s: +(steps * DT).toFixed(3), settled: settledAtStep !== null, settled_at_step: settledAtStep,
      gravity_m_s2: GRAVITY, engine: "Rapier 0.14 (WASM)", meters_per_scene_unit: m, support: staticMeta,
      scale_is_physical: !!(config && config.physicalScale),
      bodies: bodies.filter((b) => !b.isStatic).map((b) => {
        const t = b.body.translation(), v = b.body.linvel();
        b.proxy.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(b.proxy.children.find((c) => c !== b.hullMesh) || b.proxy, true);
        return {
          id: b.obj.id, position_units: [t.x / m, t.y / m, t.z / m], speed_m_s: len(v), sleeping: b.body.isSleeping(),
          mass_kg: b.massKg, collider: b.kind, bottom_y_m: box.min.y * m, top_y_m: box.max.y * m, start_center_y_m: b.startY,
          quaternion: [b.body.rotation().x, b.body.rotation().y, b.body.rotation().z, b.body.rotation().w],
        };
      }),
    };
  }

  // Independent physics sanity checks in their own throw-away worlds (do not touch the scene).
  async function selfTest() {
    await initRapier();
    const out = {};
    {
      const w = new RAPIER.World({ x: 0, y: -GRAVITY, z: 0 }); w.timestep = DT;
      const b = w.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(0, 10, 0));
      w.createCollider(RAPIER.ColliderDesc.ball(0.05).setMass(1), b);
      for (let i = 0; i < 30; i++) w.step(); // 0.5 s
      const fell = 10 - b.translation().y;
      const expected = 0.5 * GRAVITY * 0.5 * 0.5;
      out.free_fall = { fell_m: fell, expected_m: expected, rel_err: Math.abs(fell - expected) / expected };
      w.free();
    }
    {
      const v = [];
      for (const m of [1, 4]) {
        const w = new RAPIER.World({ x: 0, y: 0, z: 0 }); w.timestep = DT;
        const b = w.createRigidBody(RAPIER.RigidBodyDesc.dynamic());
        w.createCollider(RAPIER.ColliderDesc.ball(0.05).setMass(m), b);
        b.applyImpulse({ x: 2, y: 0, z: 0 }, true);
        w.step();
        v.push(b.linvel().x);
        w.free();
      }
      out.mass_impulse = { v_mass1: v[0], v_mass4: v[1], ratio: v[0] / v[1], expected_ratio: 4 };
    }
    {
      // a box collider rests with its bottom exactly on the ground: shape size changes the resting height
      const heights = [];
      for (const hy of [0.05, 0.12]) {
        const w = new RAPIER.World({ x: 0, y: -GRAVITY, z: 0 }); w.timestep = DT;
        w.createCollider(RAPIER.ColliderDesc.cuboid(5, 0.5, 5).setTranslation(0, -0.5, 0));
        const b = w.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(0, 0.6, 0));
        w.createCollider(RAPIER.ColliderDesc.cuboid(0.1, hy, 0.1).setMass(1), b);
        for (let i = 0; i < 240; i++) w.step();
        heights.push({ half_height: hy, rest_center_y: b.translation().y });
        w.free();
      }
      out.collider_shape = heights;
    }
    return out;
  }

  return { start, stepN, tick, reset, state, selfTest, isRunning: () => running, simRoot, hasSettled: () => settledAtStep !== null };
}
