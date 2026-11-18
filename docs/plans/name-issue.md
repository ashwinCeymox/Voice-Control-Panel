CORE ISSUE:MODEL CLASSIFCATION NEED TO BE PROPER

#SPEECH TO TEXT PLAYGROUND
Under the streaming STT
->stremaing model subselection i see only 2 models Zipformare 20M and Sherpa ONNX 
 my newly onstalled streaming models is not shown here the stt classifcation is not proper.
->fix :my suggestion is to check the model metadata which contains whether the model is stremaing or not. 

#TEXT TO SPEEH PLAYGORUND
->In the voice configuration is shows two options installed TTS models and Voices
  ->all the voice uses Kokoro model with 82 Million parameter the other models are just dummy.

#API DOCUMENTATION AND CODE SNIPPETS
-> in LLM chat streaming under model section it blindly shows all the model not only for llm chat streaming but for all sections (this needs to be filterd)
-> also there are issues with try it returning 502 error code check it.

#Request Logs 
-> the key/profile is shown as none (a proper user given name should be show else show that it was system generated.) dont keep it none and give a new filter based on the key/profile name.

#Overview

the graph is shown as empty.
->Warn Engine and Memory sections feels hard coded. here the currently active models can been shown realtime, and the models that are junks (models which remains in the memory but not used for a quite while lets say 5 min - 7min) these models be listed and given an option to flush out these form the memory / or auto flushing mechanism can be implemented ina intervel or 10 min . giving a live pop up with themodel name.
->in model activity sections the key anme can be shown based on the requests. showing the metadata such as latency 
.
