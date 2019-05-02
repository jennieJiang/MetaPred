""" Code for the Main function of MetaPred. """
import os, csv
import numpy as np
import random
import pickle as pkl
import tensorflow as tf
import copy
from tensorflow.python.platform import flags

from data_loader import DataLoader
import model, finetune


FLAGS = flags.FLAGS
flags.DEFINE_string('source', 'AD', 'source task')
flags.DEFINE_string('target', 'MCI', 'simulated task')
flags.DEFINE_string('true_target', 'PD', 'true task')

## Dataset/method options
flags.DEFINE_integer('n_classes', 2, 'number of classes used in classification (e.g. binary classification)')

## Training options
flags.DEFINE_string('method', 'rnn', 'deep learning methods for modeling')
flags.DEFINE_integer('pretrain_iterations', 20000, 'number of pre-training iterations')
flags.DEFINE_integer('metatrain_iterations', 10000, 'number of metatraining iterations') # 15k for omniglot, 50k for sinusoid
flags.DEFINE_integer('meta_batch_size', 8, 'number of tasks sampled per meta-update')
flags.DEFINE_integer('update_batch_size', 16, 'number of samples used for inner gradient update (K for K-shot learning)')
flags.DEFINE_float('meta_lr', 0.0001, 'the base learning rate of the generator')
flags.DEFINE_float('update_lr', 1e-3, 'step size alpha for inner gradient update')
flags.DEFINE_integer('num_updates', 4, 'number of inner gradient updates during training')
flags.DEFINE_integer('n_total_batches', 100000, 'total batches generated by random sampling')


## Model options
flags.DEFINE_string('norm', 'None', 'batch_norm, layer_norm, or None')
flags.DEFINE_bool('stop_grad', False, 'if True, do not use second derivatives in meta-optimization (for speed)')
flags.DEFINE_bool('isReg', True, 'if True, compute regularization of weights and bias')
flags.DEFINE_float('dropout', 0.5, 'drop out when modeling, with probability keep_prob')

## Logging, saving, and testing options
flags.DEFINE_integer('run_time', 1, 're-run for stable analysis')
flags.DEFINE_bool('train', True, 'True to train, False to test directly')
flags.DEFINE_bool('test', True, 'True to test, no matter the model is trained')
flags.DEFINE_bool('finetune', False, 'True to finetunning furthermore, after meta-learning')
flags.DEFINE_bool('log', True, 'if false, do not log summaries, for debugging code')
flags.DEFINE_string('logdir', 'model/', 'directory for summaries and checkpoints')
flags.DEFINE_bool('resume', False, 'resume training if there is a model available')
flags.DEFINE_integer('test_iter', -1, 'iteration to load model (-1 for latest model)')
flags.DEFINE_integer('train_update_batch_size', -1, 'number of examples used for gradient update during training (use if you want to test with a different number)')
flags.DEFINE_float('train_update_lr', -1, 'value of inner gradient step step during training. (use if you want to test with a different value)') # 0.1 for omniglot


def train(data_loader, ifold, exp_string):
    # construct MetaPred model
    print ("constructing MetaPred model ...")
    m1 = model.MetaPred(data_loader, FLAGS.meta_lr, FLAGS.update_lr)
    # fitting the meta-learning model
    print ("model training...")
    sess = m1.fit(data_loader.episode, data_loader.episode_val[ifold], ifold, exp_string)
    return m1, sess


def test(data_loader, ifold, m, sess, exp_string):
    # meta-testing the model
    print ("model test...")
    data_tuple_val = (data_loader.data_s, data_loader.data_tt_val[ifold], data_loader.label_s, data_loader.label_tt_val[ifold])
    test_accs, test_aucs, test_ap, test_f1s = m.evaluate(data_loader.episode_val[ifold], data_tuple_val, sess=sess, prefix="metatest_")
    print('Test results: ' + "ifold: " + str(ifold) + ": tAcc: " + str(test_accs) + \
               ", tAuc: " + str(test_aucs) + ", tAP: "  + str(test_ap) + ", tF1: "  + str(test_f1s))
    return test_accs, test_aucs, test_ap, test_f1s


def fine_tune(data_loader, ifold, meta_m, exp_string):
    # construct MetaPred model
    is_finetune = True
    print ("finetunning MetaPred model ...")
    if FLAGS.method == "mlp":
        m2 = finetune.MLP(data_loader, meta_m, freeze_opt=freeze_opt, is_finetune=is_finetune)
    if FLAGS.method == "cnn":
        m2 = finetune.CNN(data_loader, meta_m, freeze_opt=freeze_opt, is_finetune=is_finetune)
    if FLAGS.method == "rnn":
        m2 = finetune.RNN(data_loader, meta_m, freeze_opt=freeze_opt, is_finetune=is_finetune)
    print ("model finetunning...")

    # model finetunning
    sess, _, _ = m2.fit(data_loader.tt_sample[ifold], data_loader.tt_label[ifold],
                  data_loader.tt_sample_val[ifold], data_loader.tt_label_val[ifold])
    return m2, sess


def save_results(metatest, exp_string):
    out_filename = "results/res_" + exp_string
    with open(out_filename, 'w') as f:
        writer = csv.writer(f, delimiter=',')
        for key in metatest:
            writer.writerow([np.mean(np.array(metatest[key]))])
            writer.writerow([np.std(np.array(metatest[key]))])
    print ("results saved")


def main():
    print (FLAGS.method)
    # set source and simulated target for training
    print ('task setting: ')
    source = [FLAGS.source]
    target = [FLAGS.target]
    true_target = [FLAGS.true_target]

    print ("The applied source tasks are: ", " ".join(source))
    print ("The simulated target task is: ", " ".join(target))
    print ("The true target task is: ", " ".join(true_target))
    n_tasks = len(source) + len(target)
    

    # load ehrs data
    data_loader = DataLoader(source, target, true_target, n_tasks,
                             FLAGS.update_batch_size, FLAGS.meta_batch_size)

    exp_string = 'stsk_'+str('&'.join(source))+'ttsk_'+str('&'.join(target))+'.mbs_'+str(FLAGS.meta_batch_size) + \
                       '.ubs_' + str(FLAGS.update_batch_size) + '.numstep' + str(FLAGS.num_updates) + '.updatelr' + str(FLAGS.update_lr)

    metatest = {'aucroc': [], 'avepre': [], 'f1score': []} # n_fold result
    n_fold = data_loader.n_fold
    for ifold in range(n_fold):
        print ("----------The %d-th fold-----------" %(ifold+1))
        meta_model = None

        if FLAGS.train:
            meta_model, sess = train(data_loader, ifold, exp_string)

        if FLAGS.finetune:
             model, sess = fine_tune(data_loader, ifold, meta_model, exp_string)

        if FLAGS.test:
            _, test_aucs, test_ap, test_f1s = test(data_loader, ifold, meta_model, sess, exp_string)
            metatest['aucroc'].append(test_aucs)
            metatest['avepre'].append(test_ap)
            metatest['f1score'].append(test_f1s)

    # show results
    print ('--------------- model setting ---------------')
    print('source: ', " ".join(source), 'simulated target: ', " ".join(target), 'true target: ', " ".join(true_target))
    print('method:', 'meta-' + FLAGS.method, 'meta-bz:', FLAGS.meta_batch_size, 'update-bz:', FLAGS.update_batch_size, \
          'num update:', FLAGS.num_updates, 'meta-lr:', FLAGS.meta_lr, 'update-lr:', FLAGS.update_lr)

    print ('--------------- 5fold results ---------------')
    print ('aucroc mean: ', np.mean(np.array(metatest['aucroc'])))
    print ('aucroc std: ', np.std(np.array(metatest['aucroc'])))
    print ('f1score mean: ', np.mean(np.array(metatest['f1score'])))
    print ('f1score std: ', np.std(np.array(metatest['f1score'])))
    save_results(metatest, exp_string)

if __name__ == "__main__":
    main()
