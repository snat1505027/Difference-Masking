"""
Finetunes TVQA.

I used a v3-32 for this (it's more expensive than VCR due to the use of video + sound data)
"""

import sys

sys.path.append('../../')
import yaml
from datetime import datetime
import pytz
import jax
import jax.numpy as jnp
from pretrain.dataloader import input_fn_builder, MASK, encoder, AUDIOSPAN
from finetune.common_dataloader import finetune_input_fn_builder, finetune_val_input_fn_builder
from mreserve.modeling import MerlotReserve

from flax.training import train_state, early_stopping
from flax.training import train_state
from flax import jax_utils
import flax.linen as nn
from finetune.optimization import construct_finetuning_train_state, finetune_train_step
from mreserve.checkpoint import save_checkpoint, load_checkpoint, bf16_to_f32, f32_to_bf16, delete_prev_checkpoint
import argparse
import pandas as pd
import numpy as np
from flax.core.frozen_dict import freeze
from copy import deepcopy
import clu.parameter_overview
import functools
import time
import os
from dotenv import load_dotenv
import math
import logging
# from jax.experimental.host_callback import call, id_print


load_dotenv('../../.env')

jax.config.update('jax_log_compiles', True)
is_on_gpu = any([x.platform == 'gpu' for x in jax.local_devices()])
print('JAX process: {} / {}. Local devices {}. Using {}'.format(
    jax.process_index(), jax.process_count(), jax.local_devices(), 'GPU' if is_on_gpu else 'TPU'), flush=True)

parser = argparse.ArgumentParser(description='Train model!')

parser.add_argument(
    'pretrain_config_file',
    help='Where the config.yaml is located',
    type=str,
)
# parser.add_argument(
#     'ckpt',
#     help='checkpoint to use',
#     type=str,
# )
parser.add_argument(
    '--lr',
    help='lr',
    type=float,
)
parser.add_argument(
    '--ne',
    help='ne',
    type=int,
    default=5,
)
parser.add_argument(
    '--output_grid_h',
    help='output_grid_h',
    type=int,
    default=12,
)
parser.add_argument(
    '--output_grid_w',
    help='output_grid_w',
    type=int,
    default=20,
)
parser.add_argument(
    '--output_name',
    help='output_name',
    type=str,
    default='',
)
parser.add_argument(
    '--alpha',
    help='alpha',
    type=float,
    default=0.0,
)
parser.add_argument(
    '--wandb_name',
    help='wandb_name',
    type=str,
    default='merlotreserve-siq-finetune',
)
parser.add_argument(
    '--joint_proj',
    help='joint_projection',
    type=str, 
    choices=['joint_proj', 'no_proj'], 
    default='no_proj',
)
parser.add_argument(
    '--percent_data',
    help='percent_data',
    type=float,
    default=1.0,
)
parser.add_argument(
    '--wandb_run_name',
    help='wandb_run_name',
    type=str,
    default='tvqa_1.0',
)
parser.add_argument(
    '--extn',
    help='extension',
    type=str,
    default='',
)
parser.add_argument(
    '--output_ext',
    help='output_extension',
    action='store_true',
    default=False,
)
parser.add_argument(
    '--no_wandb',
    help='no_wandb',
    action='store_true',
    default=False,
)
parser.add_argument(
    '--mask_where',
    help='mask_where',
    type=str,
    default='no_mask',
)
parser.add_argument(
    '--val_batch_size',
    help='val_batch_size -- defaults to 32',
    type=int,
    default=32,
)
parser.add_argument(
    '-scan_minibatch',
    help='scan_minibatch -- basically, if this is true then batch size is 1 but we do gradient accumulation',
    action='store_true',
    default=False,
)
args = parser.parse_args()

if args.mask_where == "random":
    args.alpha = 0.0

proj_stat = args.joint_proj #"joint_proj" if args.joint_proj else "no_proj"
ext = "tvqa_"+args.mask_where+"_"+ str(args.percent_data)+ "_"+str(args.output_grid_h)+'_'+str(args.output_grid_w)+'_'+str(args.alpha)+'_'+proj_stat+args.extn

# print(f"Loading from {args.config_file}", flush=True)
with open(args.pretrain_config_file, 'r') as f:
    config = yaml.load(f, yaml.FullLoader)


TOTAL_SIZE = 274 * 64
PERCENT_DATASET = args.percent_data
RECORD_PER_FILE = 274

# config['data']['train_fns'] = os.path.join(os.environ["TFRECORDS_PATH"], "train{:03d}of256.tfrecord")
config['data']['train_fns'] = os.path.join(os.environ["TFRECORDS_PATH"], "train{:03d}of064.tfrecord")
config['data']['num_train_files'] = math.ceil((TOTAL_SIZE * PERCENT_DATASET) / RECORD_PER_FILE) # 256
config['data']['num_answers'] = 5
config['data']['random_scale_max'] = 1.1
config['data']['random_scale_min'] = 1.0
config['data']['num_segments'] = 7
config['data']['mask_where'] = args.mask_where
config['data']['alpha'] = args.alpha

config['device']['batch_size'] = 8
config['device']['prefetch_size'] = 0
config['device']['n_fns_per_cycle'] = 64 #256

NUM_EPOCH = args.ne

TRAIN_SIZE = config['data']['num_train_files'] * RECORD_PER_FILE if PERCENT_DATASET < 1.0 else TOTAL_SIZE
steps_per_epoch = TRAIN_SIZE // config['device']['batch_size']
config['optimizer'] = {
    'beta_2': 0.98,
    'eps': 1e-6,
    'learning_rate': args.lr,
    'num_train_steps': NUM_EPOCH * steps_per_epoch,
    'num_warmup_steps': int(0.5 * steps_per_epoch),
    'use_bfloat16_adam': True,
    'weight_decay_rate': 0.1,
    'do_bias_correction': True,
}

# print(TRAIN_SIZE, config['data']['num_train_files'], steps_per_epoch)

config['device']['iterations_per_loop'] = steps_per_epoch
config['data']['lang_seq_len'] = 256
cfg_name = args.pretrain_config_file.split('/')[-1]
seattle_time = pytz.utc.localize(datetime.utcnow()).astimezone(pytz.timezone('America/Los_Angeles'))
seattle_time = seattle_time.strftime("%Y-%m-%d-%H:%M.%S")

config['device']['output_dir'] = os.path.join(os.environ["OUTPUT_PATH"], cfg_name)
if args.output_name != '':
    config['device']['output_dir'] = os.path.join(config['device']['output_dir'], args.output_name)
if args.output_ext:
    config['device']['output_dir'] = os.path.join(config['device']['output_dir'], ext)
else:
    config['device']['output_dir'] = os.path.join(config['device']['output_dir'], seattle_time)

np.random.seed(123456)
    
data_ckpt = {0.01: '534', 0.02: '1071', 0.1: '4650', 0.2: '9300', 0.3: '13773', 0.5: '22896', 1.0: '45792'}
# config['_ckpt'] = '/home/sakter/results/retrain/out_rerun/base.yaml/'+ext+'/ckpt_'+data_ckpt[args.percent_data] 

##############
ckpt_dir = '/home/sakter/results/retrain_siq/out_clip_tf_idf/base.yaml/'+ext
onlyfiles = [os.path.join(ckpt_dir, f) for f in os.listdir(ckpt_dir) if os.path.isfile(os.path.join(ckpt_dir, f))]
assert len(onlyfiles) == 1
config['_ckpt'] = onlyfiles[0]

# config['_ckpt'] = '/home/sakter/results/retrain/out_rerun/base.yaml/tvqa_random_0.1_12_24_0.0_no_proj_run_4/ckpt_4650'

if not os.path.exists(config['_ckpt']):
    print("This checkpoint is not in the directory!")
    sys.exit()

LOG = f"/home/sakter/results/retrain_siq/finetune_clip_tf_idf/{ext}.log"    

if os.path.exists(LOG):
    print("This is already done!")
    sys.exit()

logging.basicConfig(filename=LOG, filemode="w", level=logging.INFO, force=True)

config['model']['output_grid'] = [args.output_grid_h, args.output_grid_w]
ds_train_iter = finetune_input_fn_builder(config, 'tvqa')
# _, dummy_batch = next(ds_train_iter)
# import pdb; pdb.set_trace()

#args.ckpt
tags = [args.mask_where]
tags.append("TPU4")
tags.append("alpha="+str(args.alpha))
tags.append(str(args.percent_data))
tags.append(proj_stat)
if args.output_name != '':
    tags.append(args.output_name)
if (jax.process_index() == 0) and not args.no_wandb:
    import wandb
    wandb_run_name = ext # args.wandb_run_name
    wandb.init(config=config, project=args.wandb_name, entity='snat', name=wandb_run_name, notes=f'Loaded from {cfg_name}', tags=tags)
else:
    wandb = None

class MerlotReserveTVQA(MerlotReserve):
    def setup(self):
        super().setup()
        # import pdb; pdb.set_trace()
        self.proj = nn.Dense(features=1, dtype=self.dtype, kernel_init=jax.nn.initializers.normal(stddev=0.02), name='proj',
                             use_bias=False)

    def __call__(self, batch):

        # Encode images (twice)
        # import pdb; pdb.set_trace()
        batch_size, images_per_batch, seq_size, img_dim = batch['images'].shape
        imgs_enc = self.vision_encoder(batch['images'].reshape(batch_size * images_per_batch, seq_size, img_dim))['seq_attnpool']
        imgs_enc = imgs_enc.reshape(batch_size, images_per_batch, seq_size // 4, self.hidden_size)

    
        # Add the "first image"
        imgs_enc = jnp.concatenate([
            jnp.zeros([batch_size, 1, seq_size // 4, self.hidden_size], dtype=imgs_enc.dtype),
            imgs_enc,
        ], 1)

        # duplicate so that we have one per answer
        images_per_batch += 1
        batch_size, num_ans_per, joint_seq_len, two_ = batch['textonly_seqs'].shape
        imgs_enc = imgs_enc.reshape(batch_size, images_per_batch * seq_size // 4, self.hidden_size).repeat(num_ans_per, axis=0)

        #########################
        text_toks = batch['textonly_seqs'][..., 0].reshape(batch_size * num_ans_per, joint_seq_len)
        textonly_inputs = self.prepare_multimodal_inputs(
            tokens=text_toks,
            token_segment_idx=batch['textonly_seqs'][..., 1].reshape(batch_size * num_ans_per, joint_seq_len),
            vision_input=imgs_enc,
        )

        # Encode audio
        # Audio clips are provided as [batch_size, num_segments, num_audio_subsegments, audio_seq_len, num_mels]
        batch_size, num_segments, num_audio_subsegments, audio_seq_len, num_mels = batch['audio_clips'].shape
        audio_enc = self.audio_encoder(batch['audio_clips'].reshape(-1, audio_seq_len, num_mels))['seq_attnpool']

        _, audio_token_len, hidden_size = audio_enc.shape
        num_audio_spans = num_segments * num_audio_subsegments

        audio_enc = audio_enc.reshape(batch_size, num_audio_spans, audio_token_len, hidden_size)
        audio_enc = audio_enc.repeat(num_ans_per, axis=0)

        audio_toks = batch['audio_seqs'][..., 0].reshape(batch_size * num_ans_per, joint_seq_len)
        audio_pointers = (jnp.cumsum((audio_toks == AUDIOSPAN).astype(jnp.int32), -1) - 1) // audio_token_len
        audio_pointers = audio_pointers % num_audio_spans

        audio_inputs = self.prepare_multimodal_inputs(
            tokens=batch['audio_seqs'][..., 0].reshape(batch_size * num_ans_per, joint_seq_len),
            token_segment_idx=batch['audio_seqs'][..., 1].reshape(batch_size * num_ans_per, joint_seq_len),
            vision_input=imgs_enc,
            audio_spans=audio_enc,
            audio_pointers=audio_pointers,
        )
        # hack: remove 'first img' from sequence lengths
        start_imgs = joint_seq_len + seq_size // 4
        for k in ['x', 'rotary_coords', 'attention_mask']:
            textonly_inputs[k] = jnp.concatenate([textonly_inputs[k][:, :joint_seq_len],
                                                  textonly_inputs[k][:, start_imgs:]], 1)

            audio_inputs[k] = jnp.concatenate([audio_inputs[k][:, :joint_seq_len],
                                               audio_inputs[k][:, start_imgs:]], 1)

        textonly_inputs['attention_mask'] = jnp.concatenate([textonly_inputs['attention_mask'][:, :, :joint_seq_len],
                                                             textonly_inputs['attention_mask'][:, :, start_imgs:]], 2)

        audio_inputs['attention_mask'] = jnp.concatenate([audio_inputs['attention_mask'][:, :, :joint_seq_len],
                                                          audio_inputs['attention_mask'][:, :, start_imgs:]], 2)
        
        #############################################################################################################

        # if args.disable_audio:
        #     x = textonly_inputs['x']
        #     coords = textonly_inputs['rotary_coords']
        #     attnmask = textonly_inputs['attention_mask']
        #     joint_enc = self.joint_transformer(x, rotary_coords=coords, attention_mask=attnmask)['seq']
        #     joint_enc = joint_enc[:, :joint_seq_len].reshape(batch_size * num_ans_per, joint_seq_len, self.hidden_size)
        #
        #     pool_idx = jnp.argmax((text_toks == MASK).astype(jnp.float32), 1)
        #     pooled_h = joint_enc[jnp.arange(batch_size * num_ans_per), pool_idx]
        #     logits_from_text = jnp.squeeze(self.proj(pooled_h), -1)
        #     logits_from_text = logits_from_text.reshape(batch_size, num_ans_per)
        #     logits_from_audio = jnp.ones_like(logits_from_text)
        #     return logits_from_audio, logits_from_text


        x = jnp.concatenate([audio_inputs['x'], textonly_inputs['x']], 0)
        coords = jnp.concatenate([audio_inputs['rotary_coords'], textonly_inputs['rotary_coords']], 0)
        attnmask = jnp.concatenate([audio_inputs['attention_mask'], textonly_inputs['attention_mask']], 0)

        joint_enc = self.joint_transformer(x, rotary_coords=coords, attention_mask=attnmask)['seq']
        joint_enc = joint_enc[:, :joint_seq_len].reshape(batch_size * 2 * num_ans_per, joint_seq_len, self.hidden_size)

        # Pool from the right tokens
        pool_idx = jnp.argmax((jnp.concatenate([audio_toks, text_toks], 0) == MASK).astype(jnp.float32), 1)
        pooled_h = joint_enc[jnp.arange(batch_size * 2 * num_ans_per), pool_idx]
        joint_enc = jnp.squeeze(self.proj(pooled_h), -1)

        logits_from_audio, logits_from_text = jnp.split(joint_enc, 2, axis=0)
        logits_from_audio = logits_from_audio.reshape(batch_size, num_ans_per)
        logits_from_text = logits_from_text.reshape(batch_size, num_ans_per)

        return logits_from_audio, logits_from_text


model = MerlotReserveTVQA.from_config(config)


params = load_checkpoint(config['_ckpt'])['params'] #load_checkpoint(args.ckpt)['params']

# Don't need those
for k in ['head', 'span_encoder']:
    params.pop(k, None)

hsz = params['joint_transformer']['final_ln']['bias'].shape[0]
params['proj'] = {'kernel': np.random.randn(hsz, 1).astype(np.float32) * 0.01}
params = freeze(params)

state, tx_fns = construct_finetuning_train_state(opt_config=config['optimizer'], model=model, params=params)

def train_loss_fn(state, params, batch):
    # import pdb; pdb.set_trace()
    logits_from_audio, logits_from_text = state.apply_fn({'params': params}, batch)
    lprobs_from_audio = jax.nn.log_softmax(logits_from_audio, axis=-1)
    lprobs_from_text = jax.nn.log_softmax(logits_from_text, axis=-1)

    labels_oh = jax.nn.one_hot(batch['labels'],
                               dtype=logits_from_audio.dtype,
                               num_classes=logits_from_audio.shape[-1])

    loss_audio = -jnp.mean(jnp.sum(labels_oh * lprobs_from_audio, axis=-1))
    loss_text = -jnp.mean(jnp.sum(labels_oh * lprobs_from_text, axis=-1))

    loss = loss_audio + loss_text
    is_right_audio = (jnp.argmax(logits_from_audio, -1) == batch['labels']).astype(jnp.float32).mean()
    is_right_text = (jnp.argmax(logits_from_text, -1) == batch['labels']).astype(jnp.float32).mean()

    return loss, {'is_right_audio': is_right_audio, 'is_right_text': is_right_text,
                  'loss_audio': loss_audio, 'loss_text': loss_text,}


p_train_step = jax.pmap(functools.partial(finetune_train_step, loss_fn=train_loss_fn, tx_fns=tx_fns, scan_minibatch=args.scan_minibatch), axis_name='batch', donate_argnums=(0,1))

def pred_step(state: train_state.TrainState, batch):
    logits_from_audio, logits_from_text = state.apply_fn({'params': state.params}, batch)

    out = {'logprobs_audio': jax.nn.log_softmax(logits_from_audio, axis=-1),
            'preds_audio': jnp.argmax(logits_from_audio, -1),
            'logprobs_text': jax.nn.log_softmax(logits_from_text, axis=-1),
            'preds_text': jnp.argmax(logits_from_text, -1),
            }
    softmax_joint = jax.nn.softmax(logits_from_audio, axis=-1) + jax.nn.softmax(logits_from_text, axis=-1)
    out['preds_joint'] = jnp.argmax(softmax_joint, -1)
    return out


p_pred_step = jax.pmap(pred_step, axis_name='batch', donate_argnums=(1,))


def val_epoch(state: train_state.TrainState):
    """
    perform a validation epoch
    :param state:
    :return:
    """
    val_config = deepcopy(config)
    # val_config['data']['val_fns'] = os.path.join(os.environ["TFRECORDS_PATH"], "val{:03d}of008.tfrecord")
    # val_config['data']['num_val_files'] = 8
    val_config['data']['val_fns'] = os.path.join(os.environ["TFRECORDS_PATH"], "val{:03d}of008.tfrecord")
    val_config['data']['num_val_files'] = 8
    val_config['data']['do_random_scale'] = False
    val_config['data']['batch_size'] = args.val_batch_size

    val_iter = finetune_val_input_fn_builder(val_config, 'tvqa')

    text_preds = []
    audio_preds = []
    joint_preds = []

    for ids, batch in val_iter:
        val_pred = p_pred_step(state, batch)
        preds_joint = val_pred['preds_joint'].reshape(-1)
        preds_audio = val_pred['preds_audio'].reshape(-1)
        preds_text = val_pred['preds_text'].reshape(-1)

        labels = batch['labels'].reshape(-1)
        for (p_j, p_a, p_t, id_i, label_i) in zip(val_pred['preds_joint'].reshape(-1),
                                                  val_pred['preds_audio'].reshape(-1),
                                                  val_pred['preds_text'].reshape(-1), ids, labels):
            if id_i == 'pad':
                continue
            text_preds.append({'pred': p_t, 'label': label_i, 'id': id_i})
            audio_preds.append({'pred': p_a, 'label': label_i, 'id': id_i})
            joint_preds.append({'pred': p_j, 'label': label_i, 'id': id_i})

    text_preds = pd.DataFrame(text_preds)
    text_preds['is_right'] = text_preds['pred'] == text_preds['label']
    text_acc = text_preds['is_right'].mean()

    audio_preds = pd.DataFrame(audio_preds)
    audio_preds['is_right'] = audio_preds['pred'] == audio_preds['label']
    audio_acc = audio_preds['is_right'].mean()

    joint_preds = pd.DataFrame(joint_preds)
    joint_preds['is_right'] = joint_preds['pred'] == joint_preds['label']
    joint_acc = joint_preds['is_right'].mean()
    return {'text_acc': text_acc, 'audio_acc': audio_acc, 'joint_acc': joint_acc}

train_metrics = []
log_every = config['device'].get('commit_every_nsteps', 50)
time_elapsed = []
epoch_elapsed = 0
best_acc = 0
early_stop = early_stopping.EarlyStopping(min_delta=1e-4, patience=5)
prev_step = 0

# the + 1 is because for some reason it crashes at the end otherwise. why? idk/
for n in range(config['optimizer']['num_train_steps']+100):
    st = time.time()
    id_, batch = next(ds_train_iter)
    state, loss_info = p_train_step(state, batch)

    if jax.process_index() == 0:
        train_metrics.append(jax.tree_map(lambda x: x[0], loss_info))
        jax.tree_map(lambda x: x.copy_to_host_async(), train_metrics[-1])

        step_for_logging = n - log_every
        if step_for_logging >= 0:
            train_metrics[step_for_logging] = {k: float(v) for k, v in train_metrics[step_for_logging].items()}
            wandb.log(train_metrics[step_for_logging], step=step_for_logging, commit=(n + 1) % log_every == 0)

        if (n + 1) % config['device']['iterations_per_loop'] == 0:
            print("Done 1 epoch", flush=True)
            logging.info("Done 1 epoch")
            epoch_elapsed += 1
            val_info = val_epoch(state)
            print(f"Saving @iter {n:03d}.\nInfo: {pd.Series(val_info)}\n~\n", flush=True)
            logging.info(f"Saving @iter {n:03d}.\nInfo: {pd.Series(val_info)}\n~\n")
            
            if wandb is not None:
                wandb.log({k + '_val': v for k, v in val_info.items()}, step=step_for_logging, commit=True)
                wandb.run.summary['joint_acc_val_'+str(epoch_elapsed)] =  val_info['joint_acc']
                wandb.run.summary['audio_acc_val_'+str(epoch_elapsed)] =  val_info['audio_acc']
                wandb.run.summary['text_acc_val_'+str(epoch_elapsed)] =  val_info['text_acc']
                if best_acc < val_info['joint_acc']:
                    best_acc = val_info['joint_acc']
                if args.ne == epoch_elapsed:
                    wandb.run.summary['joint_acc_val'] =  val_info['joint_acc']
                    wandb.run.summary['audio_acc_val'] =  val_info['audio_acc']
                    wandb.run.summary['text_acc_val'] =  val_info['text_acc']
                    wandb.run.summary['best_joint_acc_val'] =  best_acc
                    
            has_improved, early_stop = early_stop.update(val_info['joint_acc']*(-1.0))
            if early_stop.should_stop:
                print('Met early stopping criteria, breaking...')
                break           
            ## Adding Early Stopping
            
            if has_improved or epoch_elapsed == args.ne:
                save_checkpoint(state, path=config['device']['output_dir'], no_optimizer=True)
                if prev_step != 0:
                    delete_prev_checkpoint(prev_step, config['device']['output_dir'])
                prev_step = int(state.step[0])

        time_elapsed.append(time.time() - st)
        if len(time_elapsed) >= 100:
            tsum = sum(time_elapsed)
            print("Completed 100 batches in {:.3f}sec, avg {:.3f} it/sec".format(tsum, 100.0 / tsum), flush=True)
            logging.info(f"Completed 100 batches in {tsum:.3f}sec, avg {100.0 / tsum:.3f} it/sec")
            time_elapsed = []
