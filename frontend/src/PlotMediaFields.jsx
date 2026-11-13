import React,{useState,useRef} from "react";
const API=import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const faces=["North","South","East","West","North-East","North-West","South-East","South-West"];
export function PlotMediaFields({value,onChange}){
 const [error,setError]=useState(""),[loading,setLoading]=useState(false);
 const fileInput=useRef(null);
 const update=(k,v)=>onChange({...value,[k]:v});
 const preview=value.remove_photo?null:value.photo_data?"data:image/jpeg;base64,"+value.photo_data:value.photo_url?API+value.photo_url:null;
 async function selectPhoto(e){
  const file=e.target.files?.[0];if(!file)return;setError("");
  if(!["image/jpeg","image/png","image/webp"].includes(file.type)||file.size>2*1024*1024){setError("Choose a JPEG, PNG or WebP photo up to 2 MB.");e.target.value="";return;}
  setLoading(true);try{
   const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=reject;reader.readAsDataURL(file)});
   onChange(old=>({...old,photo_data:data,remove_photo:false}));
  }catch{setError("Could not read this image. Please try another photo.");}finally{setLoading(false);}
 }
 return <fieldset className="plot-media-fields"><legend>Property photos &amp; listing details</legend><p className="muted">Only publish photos and information you have permission to use. All fields below are optional and visible to customers.</p><label>Property cover photo (JPEG, PNG or WebP, up to 2 MB)<input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" onChange={selectPhoto} disabled={loading}/></label>{error&&<p role="alert" className="notice">{error}</p>}{preview&&<div className="plot-photo-preview"><img src={preview} alt="Property cover preview"/><button className="outline" type="button" onClick={()=>{onChange(old=>({...old,photo_data:null,remove_photo:true}));if(fileInput.current)fileInput.current.value=""}}>Remove photo</button></div>}<small>{loading?"Reading photo…":"The photo is saved with the plot when you submit. Existing photo stays unless replaced or removed."}</small><label>Property description<textarea rows="4" maxLength="2000" value={value.description||""} onChange={e=>update("description",e.target.value)} placeholder="Describe the development, access and surroundings"/></label><label>Amenities<textarea rows="3" maxLength="1000" value={value.amenities||""} onChange={e=>update("amenities",e.target.value)} placeholder="Park, security, water supply, paved roads"/></label><label>Approvals / legal information<input maxLength="500" value={value.approvals||""} onChange={e=>update("approvals",e.target.value)} placeholder="Verified approval or registration reference"/></label><label>Possession / registration readiness<input maxLength="120" value={value.possession||""} onChange={e=>update("possession",e.target.value)} placeholder="Verified readiness or expected date"/></label><label>Plot facing<select value={value.facing||""} onChange={e=>update("facing",e.target.value)}><option value="">Not verified</option>{faces.map(x=><option key={x}>{x}</option>)}</select></label><label>Road width (ft)<input type="number" min="0.01" max="500" step="any" value={value.road_width_ft??""} onChange={e=>update("road_width_ft",e.target.value===""?null:Number(e.target.value))}/></label></fieldset>
}
export function PropertyDetails({plot}){
 return <div className="listing-details">{plot.description&&<p className="listing-description">{plot.description}</p>}{(plot.facing||plot.road_width_ft||plot.possession||plot.approvals||plot.amenities)&&<dl>{plot.facing&&<><dt>Facing</dt><dd>{plot.facing}</dd></>}{plot.road_width_ft!=null&&<><dt>Road width</dt><dd>{plot.road_width_ft} ft</dd></>}{plot.possession&&<><dt>Possession</dt><dd>{plot.possession}</dd></>}{plot.approvals&&<><dt>Approvals (as listed)</dt><dd>{plot.approvals}</dd></>}{plot.amenities&&<><dt>Amenities</dt><dd>{plot.amenities}</dd></>}</dl>}</div>
}
export function PropertyCover({plot}){
 const [failed,setFailed]=useState(false);
 if(plot.photo_url&&!failed)return <img className="property-cover" src={API+plot.photo_url} alt={plot.project+" — plot "+plot.plot_number} loading="lazy" decoding="async" onError={()=>setFailed(true)}/>;
 return <div className="property-visual" aria-hidden="true"><span>Plot {plot.plot_number}</span><i/><small>Photo not supplied · schematic layout available</small></div>
}
