import React,{useEffect,useRef,useState} from "react";
const API=import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const MAX=2*1024*1024;
export function ListingPhoto({plot,className=""}){
 const [failed,setFailed]=useState(false);
 const src=plot?.photo_url?API+plot.photo_url:null;
 useEffect(()=>setFailed(false),[src,plot]);
 if(!src||failed)return null;
 return <img className={"listing-photo "+className} src={src} alt={plot.project+" · Plot "+plot.plot_number} loading="lazy" decoding="async" onError={()=>setFailed(true)}/>;
}
export default function PlotListingFields({value,onChange,disabled=false}){
 const [error,setError]=useState(""),[reading,setReading]=useState(false);
 const fileRef=useRef(null),generation=useRef(0);
 useEffect(()=>()=>{generation.current++},[]);
 function update(k,v){onChange({...value,[k]:v})}
 async function photoSelected(e){
   const file=e.target.files?.[0];if(!file)return;
   const current=++generation.current;
   setError("");
   if(file.size>MAX||!["image/jpeg","image/png","image/webp"].includes(file.type)){
     setError("Choose a JPEG, PNG or WebP photo up to 2 MB.");e.target.value="";return;
   }
   setReading(true);
   try{
     const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsDataURL(file)});
     if(generation.current!==current)return;
     onChange(v=>({...v,photo_data:data.split(",")[1],remove_photo:false}));
   }catch{if(generation.current===current)setError("Could not read this photo. Choose another file.")}
   finally{if(generation.current===current)setReading(false)}
 }
 function remove(){
   generation.current++;setReading(false);setError("");if(fileRef.current)fileRef.current.value="";
   onChange(v=>({...v,photo_data:null,remove_photo:true}));
 }
 return <fieldset className="listing-fields" disabled={disabled}><legend>Public property listing details</legend>
   <div className="photo-upload"><label>Property photo (JPEG, PNG or WebP)<input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={photoSelected} disabled={reading}/></label>
   <small>Upload an actual property photograph, up to 2 MB. It will be resized and published on the customer website. Do not upload identity documents, personal photos or misleading renderings.</small>
   {error&&<p role="alert" className="notice">{error}</p>}{reading&&<p role="status">Preparing photo…</p>}
   {value.photo_data?<img className="listing-photo preview" src={"data:image/jpeg;base64,"+value.photo_data} alt="Selected property photo preview"/>:!value.remove_photo&&value.photo_url?<ListingPhoto plot={value} className="preview"/>:<div className="photo-empty">No property photo selected</div>}
   {(value.photo_data||(!value.remove_photo&&value.photo_url))&&<button type="button" className="outline" onClick={remove}>Remove property photo</button>}
   </div>
   <label>Property description<textarea rows="4" maxLength="3000" value={value.description||""} onChange={e=>update("description",e.target.value)} placeholder="Describe the project, plot and nearby facilities."/></label>
   <label>Amenities<textarea rows="3" maxLength="1500" value={value.amenities||""} onChange={e=>update("amenities",e.target.value)} placeholder="Water supply, electricity, roads, parks, security…"/></label>
   <label>Approvals and documentation<textarea rows="3" maxLength="1500" value={value.approval_details||""} onChange={e=>update("approval_details",e.target.value)} placeholder="Enter verified approval authority, registration or documentation details."/></label>
   <small>Approval text is supplied by your team; customers should verify documents independently.</small>
   <label>Plot facing<select value={value.facing||""} onChange={e=>update("facing",e.target.value)}><option value="">Not verified</option>{["North","South","East","West","North-East","North-West","South-East","South-West"].map(x=><option key={x}>{x}</option>)}</select></label>
   <label>Road width (ft)<input type="number" min="1" max="1000" step="any" value={value.road_width_ft??""} onChange={e=>update("road_width_ft",e.target.value===""?null:Number(e.target.value))}/></label>
 </fieldset>
}
export function PublicListingDetails({plot}){
 if(!plot)return null;
 return <div className="public-listing-details">{(plot.facing||plot.road_width_ft!=null)&&<p className="listing-facts">{[plot.facing?plot.facing+" facing":"",plot.road_width_ft!=null?plot.road_width_ft+" ft road":""].filter(Boolean).join(" · ")}</p>}{plot.description&&<p>{plot.description}</p>}{plot.amenities&&<div><strong>Amenities</strong><p>{plot.amenities}</p></div>}{plot.approval_details&&<div><strong>Approvals and documentation</strong><p>{plot.approval_details}</p><small>Provided by the property team; verify independently.</small></div>}</div>
}
