import React,{useEffect,useRef,useState} from "react";
import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
const colors={available:0x79af7b,reserved:0xd8b96b,sold:0x9daaa1};
export default function Plot3D({plots,selected,onSelect,onFallback}){
 const mount=useRef(null),sceneRef=useRef(null),selectRef=useRef(onSelect),fallbackRef=useRef(onFallback),[failed,setFailed]=useState(false);
 selectRef.current=onSelect;fallbackRef.current=onFallback;
 const signature=plots.map(p=>[p.id,p.cx,p.cy,p.side,p.area_sqft,p.width_ft,p.depth_ft,p.status].join(":")).join("|");
 useEffect(()=>{
  const host=mount.current;
  if(!host||!plots.length)return;
  let renderer,controls,observer,scene,camera,down,up,contextLost,render;
  const meshes=[],disposables=[];
  function cleanup(){
   observer?.disconnect();controls?.removeEventListener("change",render);controls?.dispose();
   if(renderer){renderer.domElement.removeEventListener("pointerdown",down);renderer.domElement.removeEventListener("pointerup",up);renderer.domElement.removeEventListener("webglcontextlost",contextLost);renderer.dispose();renderer.domElement.remove()}
   for(const resource of disposables)resource.dispose();
   sceneRef.current=null;
  }
  try{
   // Probe WebGL2 first to avoid throwing on unsupported mobile GPUs.
   const canvas=document.createElement("canvas"),gl=canvas.getContext("webgl2",{antialias:true,alpha:false});
   if(!gl)throw new Error("WebGL2 unavailable");
   renderer=new THREE.WebGLRenderer({canvas,context:gl,antialias:true});
   renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.75));renderer.setClearColor(0xe6eee0);
   host.appendChild(renderer.domElement);renderer.domElement.setAttribute("aria-label","Interactive WebGL property model");
   scene=new THREE.Scene();scene.add(new THREE.HemisphereLight(0xffffff,0x688067,2.0));
   const light=new THREE.DirectionalLight(0xfff2df,2.4);light.position.set(80,160,100);scene.add(light);
   const bounds=new THREE.Box3();
   for(const p of plots){
    const aspect=p.width_ft&&p.depth_ft?Math.sqrt(p.width_ft/p.depth_ft):1;
    const w=p.side*aspect,d=p.side/aspect;
    const geometry=new THREE.BoxGeometry(w,5,d),material=new THREE.MeshStandardMaterial({color:p.id===selected?0xf1d38a:colors[p.status]??colors.available,roughness:.86});
    const mesh=new THREE.Mesh(geometry,material);mesh.position.set(p.cx,2.5,p.cy);mesh.userData.plot=p;scene.add(mesh);meshes.push(mesh);disposables.push(geometry,material);bounds.expandByObject(mesh);
    const edges=new THREE.EdgesGeometry(geometry),lineMat=new THREE.LineBasicMaterial({color:0x355d46}),lines=new THREE.LineSegments(edges,lineMat);lines.position.copy(mesh.position);scene.add(lines);disposables.push(edges,lineMat);
   }
   const center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3()),span=Math.max(size.x,size.z,30);
   const ground=new THREE.PlaneGeometry(span+80,span+80),groundMat=new THREE.MeshStandardMaterial({color:0xd1e1cb,roughness:1});const floor=new THREE.Mesh(ground,groundMat);floor.rotation.x=-Math.PI/2;floor.position.set(center.x,-.1,center.z);scene.add(floor);disposables.push(ground,groundMat);
   const grid=new THREE.GridHelper(span+80,16,0xb3c8ac,0xb3c8ac);grid.position.set(center.x,.02,center.z);scene.add(grid);disposables.push(grid.geometry,grid.material);
   camera=new THREE.PerspectiveCamera(45,1,.1,span*15+1000);camera.position.set(center.x+span*1.25,span*1.45,center.z+span*1.25);
   controls=new OrbitControls(camera,renderer.domElement);controls.target.copy(center);controls.minDistance=span*.25;controls.maxDistance=span*5+100;controls.maxPolarAngle=Math.PI/2-.04;controls.enableDamping=false;controls.update();
   render=()=>{renderer.render(scene,camera)};
   controls.addEventListener("change",render);
   function resize(){const w=Math.max(1,host.clientWidth),h=Math.max(1,host.clientHeight);renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();render()}
   observer=new ResizeObserver(resize);observer.observe(host);resize();
   let start=null;down=e=>{start=[e.clientX,e.clientY]};up=e=>{if(!start||Math.hypot(e.clientX-start[0],e.clientY-start[1])>7){start=null;return}start=null;const rect=renderer.domElement.getBoundingClientRect();const coords=new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);const ray=new THREE.Raycaster();ray.setFromCamera(coords,camera);const hit=ray.intersectObjects(meshes,false)[0];if(hit)selectRef.current?.(hit.object.userData.plot.id)};
   contextLost=e=>{e.preventDefault();setFailed(true);fallbackRef.current?.()};
   renderer.domElement.addEventListener("pointerdown",down);renderer.domElement.addEventListener("pointerup",up);renderer.domElement.addEventListener("webglcontextlost",contextLost);
   sceneRef.current={meshes,render,controls,camera,center,span};
  }catch{cleanup();setFailed(true);fallbackRef.current?.();return}
  return cleanup;
 },[signature]);
 useEffect(()=>{const current=sceneRef.current;if(!current)return;for(const mesh of current.meshes){const p=mesh.userData.plot;mesh.material.color.setHex(p.id===selected?0xf1d38a:colors[p.status]??colors.available)}current.render()},[selected,signature]);
 function reset(aerial=false){const v=sceneRef.current;if(!v)return;const {center,span,camera,controls,render}=v;camera.position.set(center.x+(aerial?0:span*1.25),span*(aerial?2.1:1.45),center.z+(aerial?.01:span*1.25));controls.target.copy(center);controls.update();render()}
 return <div className="webgl-model"><div className="model-stage webgl-stage" ref={mount} role="region" aria-label="Interactive 3D property scene"/>{failed?<p>3D graphics are unavailable on this device. Use the accessible schematic layout.</p>:<><div className="viewer-switch"><button type="button" className="outline" onClick={()=>reset(false)}>Reset 3D camera</button><button type="button" className="outline" onClick={()=>reset(true)}>Top-down camera</button></div><p className="muted tiny">Drag to rotate; pinch or scroll to zoom. Select a plot by tapping it or using the plot buttons below. Actual dimensions are used for aspect ratio when supplied; all coordinates remain schematic.</p></>}</div>
}
