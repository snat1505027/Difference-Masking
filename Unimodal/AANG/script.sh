#!/bin/bash
#SBATCH -p gpu_highmem
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=8
#SBATCH --mem 80GB
#SBATCH --mail-type=ALL
#SBATCH -w compute-1-5
#SBATCH --mail-user=your_email@gmail.com # TODO
#SBATCH --chdir=/work/sakter/AANG
#SBATCH --output=/work/sakter/AANG/logs/%j.out # TODO
#SBATCH --error=/work/sakter/AANG/logs/%j.err # TODO

eval "$(conda shell.bash hook)"
conda activate aang

# python -m scripts.autoaux --prim-task-id chemprot --train_data_file=datasets/chemprot/train.jsonl --dev_data_file=datasets/chemprot/dev.jsonl --test_data_file=datasets/chemprot/test.jsonl --output_dir /work/sakter/AANG/autoaux_outputs/TAPT/chemprot/auxlr=1.0.soptlr=0.1.classflr=0.0001.wfrac=0.06.nconf_samp=1.primbsz=16.auxbsz=32/seed=0 --model_type roberta-base --model_name_or_path roberta-base  --tokenizer_name roberta-base --per_gpu_train_batch_size=17  --gradient_accumulation_steps=32 --do_train --learning_rate=0.0001 --block_size 512 --logging_steps 10000 --classf_lr=0.0001 --classf_patience 20 --num_train_epochs=150  --classifier_dropout=0.3 --overwrite_output_dir --classf_iter_batchsz=8 --classf_ft_lr 1e-6 --classf_max_seq_len 512 --seed=0  --classf_dev_wd=0.1 --classf_dev_lr=0.01 -searchspace-config=tapt -task-data=datasets/chemprot/train.txt -in-domain-data=datasets/chemprot/domain.10xTAPT.txt -num-config-samples=1 --dev_batch_sz=32 --eval_every 30 -prim-aux-lr=0.1 -auxiliaries-lr=1 --classf_warmup_frac 0.06 --classf_wd 0.1 --base_wd 0.01 --dev_fit_iters 10 -step-meta-every 3 -token_temp 0.5  --classf-metric accuracy -pure-transform

python -m scripts.autoaux --prim-task-id chemprot --train_data_file=datasets/chemprot/train.jsonl --dev_data_file=datasets/chemprot/dev.jsonl --test_data_file=datasets/chemprot/test.jsonl --output_dir /work/sakter/AANG/autoaux_outputs/TAPT/chemprot/auxlr_test/seed=0 --model_type roberta-base --model_name_or_path roberta-base  --tokenizer_name roberta-base --per_gpu_train_batch_size=17  --gradient_accumulation_steps=32 --do_train --learning_rate=0.0001 --block_size 512 --logging_steps 10000 --classf_lr=0.0001 --classf_patience 20 --num_train_epochs=150  --classifier_dropout=0.3 --overwrite_output_dir --classf_iter_batchsz=8 --classf_ft_lr 1e-6 --classf_max_seq_len 512 --seed=0  --classf_dev_wd=0.1 --classf_dev_lr=0.01 -searchspace-config=tapt -task-data=datasets/chemprot/train.txt -in-domain-data=datasets/chemprot/domain.10xTAPT.txt -num-config-samples=1 --dev_batch_sz=32 --eval_every 30 -prim-aux-lr=0.1 -auxiliaries-lr=1 --classf_warmup_frac 0.06 --classf_wd 0.1 --base_wd 0.01 --dev_fit_iters 10 -step-meta-every 3 -token_temp 0.5  --classf-metric accuracy -pure-transform

# python -m scripts.autoaux --prim-task-id chemprot --train_data_file=datasets/chemprot/train.jsonl --dev_data_file=datasets/chemprot/dev.jsonl --test_data_file=datasets/chemprot/test.jsonl --output_dir /work/sakter/AANG/autoaux_outputs/TAPT/chemprot/auxlr_test/seed=0 --model_type roberta-base --model_name_or_path roberta-base  --tokenizer_name roberta-base --per_gpu_train_batch_size=17  --gradient_accumulation_steps=32 --do_train --learning_rate=0.0001 --block_size 512 --logging_steps 10000 --classf_lr=0.0001 --classf_patience 20 --num_train_epochs=150  --classifier_dropout=0.3 --overwrite_output_dir --classf_iter_batchsz=8 --classf_ft_lr 1e-6 --classf_max_seq_len 512 --seed=0  --classf_dev_wd=0.1 --classf_dev_lr=0.01 -searchspace-config=tapt -task-data=datasets/chemprot/train.txt -in-domain-data=datasets/chemprot/domain.10xTAPT.txt -num-config-samples=1 --dev_batch_sz=32 --eval_every 30 -prim-aux-lr=0.1 -auxiliaries-lr=1 --classf_warmup_frac 0.06 --classf_wd 0.1 --base_wd 0.01 --dev_fit_iters 10 -step-meta-every 3 -token_temp 0.5  --classf-metric accuracy -pure-transform
