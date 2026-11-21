/* Only loaded after the user starts their microphone. No audio output or network access. */
class LaithMicrophone extends AudioWorkletProcessor {
  constructor(){super();this.samples=new Float32Array(2048);this.used=0;}
  process(inputs,outputs){
    for(const channels of outputs)for(const channel of channels)channel.fill(0);
    const input=inputs[0]?.[0];if(!input)return true;
    for(let i=0;i<input.length;i++){
      this.samples[this.used++]=input[i];
      if(this.used===this.samples.length){this.port.postMessage(this.samples,[this.samples.buffer]);this.samples=new Float32Array(2048);this.used=0;}
    }
    return true;
  }
}
registerProcessor('laith-microphone',LaithMicrophone);
